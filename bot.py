import os
import json
import asyncio
import requests
import pandas as pd
from datetime import datetime
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
import httpx

# Загрузка .env файла если есть (для локального запуска)
env_file = Path(__file__).parent / '.env'
if env_file.exists():
    with open(env_file, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ.setdefault(key.strip(), value.strip())

# ==========================================
# НАСТРОЙКИ
# ==========================================
BOT_TOKEN      = os.environ.get('BOT_TOKEN')
CHAT_ID        = os.environ.get('CHAT_ID')
PROXY_URL      = os.environ.get('PROXY_URL')  # Опционально: socks5://user:pass@host:port
SPREADSHEET_ID = '1tHn2XnJVUYOK-PZFBRIrGvLWFun7gyh0'
SHEET_GID      = '285132150'
CHECK_INTERVAL = 300  # проверка каждые 5 минут
STATE_FILE     = 'last_state.json'

CSV_URL = f'https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/export?format=csv&gid={SHEET_GID}'


# ==========================================
# ФУНКЦИИ ДЛЯ РАБОТЫ С ТАБЛИЦЕЙ
# ==========================================
def get_column_ae() -> list:
    """Скачивает таблицу и возвращает столбец AE (для сравнения изменений)"""
    try:
        df = pd.read_csv(CSV_URL, header=None)
        if df.shape[1] > 30:
            col = df.iloc[:, 30].fillna('').astype(str).tolist()
            while col and not col[-1].strip():
                col.pop()
            return col
        return []
    except Exception as e:
        print(f'Ошибка при чтении таблицы: {e}')
        return []


def load_state() -> list:
    """Загружает предыдущее состояние из файла"""
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []


def save_state(data: list):
    """Сохраняет текущее состояние в файл"""
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)


def find_changes_detailed(old_schedule: dict, new_schedule: dict) -> list:
    """Находит изменения с указанием дня и пары"""
    changes = []
    
    all_days = set(old_schedule.keys()) | set(new_schedule.keys())
    
    for day in all_days:
        old_pairs = old_schedule.get(day, {})
        new_pairs = new_schedule.get(day, {})
        
        all_pair_nums = set(old_pairs.keys()) | set(new_pairs.keys())
        
        for pair_num in all_pair_nums:
            old_val = old_pairs.get(pair_num, '').strip()
            new_val = new_pairs.get(pair_num, '').strip()
            
            if old_val != new_val:
                changes.append({
                    'day': day,
                    'pair': pair_num,
                    'old_val': old_val or '(пусто)',
                    'new_val': new_val or '(пусто)'
                })
    
    return changes


def format_changes(changes: list) -> str:
    """Форматирует изменения для уведомления"""
    if not changes:
        return '✅ Изменений не обнаружено\\!'
    
    day_emoji = {
        'понедельник': '📘',
        'вторник': '📗',
        'среда': '📙',
        'четверг': '📕',
        'пятница': '📓',
        'суббота': '📔'
    }
    
    msg = f'🔔 *Найдено изменений: {len(changes)}*\n\n'
    
    for c in changes[:15]:  # Первые 15 изменений
        emoji = day_emoji.get(c['day'].lower(), '📖')
        msg += f'{emoji} *{escape_md(c["day"].capitalize())}*, пара {c["pair"]}\n'
        msg += f'  ❌ Было: `{escape_md(c["old_val"])}`\n'
        msg += f'  ✅ Стало: `{escape_md(c["new_val"])}`\n\n'
    
    if len(changes) > 15:
        msg += f'_\\.\\.\\. и ещё {len(changes) - 15} изменений_\n\n'
    
    return msg
    """Находит изменения между старым и новым состоянием"""
    changes = []
    max_len = max(len(old), len(new))
    for i in range(max_len):
        old_val = old[i].strip() if i < len(old) else ''
        new_val = new[i].strip() if i < len(new) else ''
        if old_val != new_val:
            changes.append({
                'row':     i + 1,
                'old_val': old_val or '_(пусто)_',
                'new_val': new_val or '_(пусто)_'
            })
    return changes


def escape_md(text: str) -> str:
    """Экранирует спецсимволы для MarkdownV2"""
    special = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    for ch in special:
        text = text.replace(ch, f'\\{ch}')
    return text
    """Скачивает таблицу и возвращает полное расписание с структурой"""
    try:
        df = pd.read_csv(CSV_URL, header=None)
        
        schedule = {}
        current_day = None
        current_pair = None
        
        for i in range(len(df)):
            col_a = str(df.iloc[i, 0]).strip() if pd.notna(df.iloc[i, 0]) else ''
            col_b = str(df.iloc[i, 1]).strip() if pd.notna(df.iloc[i, 1]) else ''
            col_ae = str(df.iloc[i, 30]).strip() if df.shape[1] > 30 and pd.notna(df.iloc[i, 30]) else ''
            
            # Определяем день недели
            if col_a and col_a.lower() in ['понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота']:
                current_day = col_a
                schedule[current_day] = {}
            
            # Определяем номер пары
            if col_b and col_b.isdigit():
                current_pair = col_b
                if current_day and current_pair:
                    schedule[current_day][current_pair] = col_ae
        
        return schedule
    except Exception as e:
        print(f'Ошибка при чтении таблицы: {e}')
        return {}


def format_schedule(schedule: dict) -> str:
    """Форматирует расписание для отображения"""
    if not schedule:
        return '📭 Расписание пусто'
    
    day_emoji = {
        'понедельник': '📘',
        'вторник': '📗',
        'среда': '📙',
        'четверг': '📕',
        'пятница': '📓',
        'суббота': '📔'
    }
    
    msg = '📅 *Текущее расписание:*\n\n'
    
    for day, pairs in schedule.items():
        emoji = day_emoji.get(day.lower(), '📖')
        msg += f'{emoji} *{escape_md(day.capitalize())}*\n'
        
        if pairs:
            for pair_num, subject in sorted(pairs.items(), key=lambda x: int(x[0])):
                if subject:
                    msg += f'  {pair_num}\\. `{escape_md(subject)}`\n'
                else:
                    msg += f'  {pair_num}\\. _\\(нет пары\\)_\n'
        else:
            msg += '  _\\(нет пар\\)_\n'
        
        msg += '\n'
    
    return msg


# ==========================================
# КОМАНДЫ БОТА
# ==========================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start"""
    keyboard = [
        [InlineKeyboardButton("📅 Показать расписание", callback_data='show_schedule')],
        [InlineKeyboardButton("🔄 Проверить изменения", callback_data='check_now')],
        [InlineKeyboardButton("ℹ️ Информация", callback_data='info')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        '👋 *Привет\\!*\n\n'
        'Я бот для мониторинга расписания\\.\n'
        'Выбери действие:',
        reply_markup=reply_markup,
        parse_mode='MarkdownV2'
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатий на кнопки"""
    query = update.callback_query
    await query.answer()
    
    if query.data == 'show_schedule':
        schedule = get_full_schedule()
        msg = format_schedule(schedule)
        
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data='back')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            msg,
            reply_markup=reply_markup,
            parse_mode='MarkdownV2'
        )
    
    elif query.data == 'check_now':
        await query.edit_message_text('⏳ Проверяю изменения\\.\\.\\.', parse_mode='MarkdownV2')
        
        current_schedule = get_full_schedule()
        current_raw = get_column_ae()
        old_raw = load_state()
        
        if not old_raw:
            save_state(current_raw)
            msg = '✅ Состояние сохранено\\. Теперь буду отслеживать изменения\\!'
        else:
            # Получаем старое расписание из сохранённых данных
            old_schedule = {}
            try:
                df_old = pd.DataFrame([old_raw]).T
                for i in range(len(df_old)):
                    # Упрощённое восстановление структуры
                    pass
            except:
                pass
            
            # Сравниваем по сырым данным
            changes_raw = find_changes(old_raw, current_raw)
            
            if changes_raw:
                # Показываем детальные изменения
                old_full = get_full_schedule()  # Текущее как "старое" для демо
                changes_detailed = find_changes_detailed(old_full, current_schedule)
                msg = format_changes(changes_detailed) if changes_detailed else format_changes(changes_raw[:10])
                save_state(current_raw)
            else:
                msg = '✅ Изменений не обнаружено\\!'
        
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data='back')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            msg,
            reply_markup=reply_markup,
            parse_mode='MarkdownV2'
        )
    
    elif query.data == 'info':
        msg = (
            'ℹ️ *Информация о боте*\n\n'
            '🔹 Проверяю расписание каждые 5 минут\n'
            '🔹 При изменении отправляю уведомление\n'
            '🔹 Можно вручную проверить через кнопки\n\n'
            f'📊 Таблица: [Открыть](https://docs\\.google\\.com/spreadsheets/d/{SPREADSHEET_ID})\n'
            f'🕐 Интервал проверки: {CHECK_INTERVAL // 60} минут'
        )
        
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data='back')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            msg,
            reply_markup=reply_markup,
            parse_mode='MarkdownV2',
            disable_web_page_preview=True
        )
    
    elif query.data == 'back':
        keyboard = [
            [InlineKeyboardButton("📅 Показать расписание", callback_data='show_schedule')],
            [InlineKeyboardButton("🔄 Проверить изменения", callback_data='check_now')],
            [InlineKeyboardButton("ℹ️ Информация", callback_data='info')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            '👋 *Привет\\!*\n\n'
            'Я бот для мониторинга расписания\\.\n'
            'Выбери действие:',
            reply_markup=reply_markup,
            parse_mode='MarkdownV2'
        )


# ==========================================
# ФОНОВАЯ ПРОВЕРКА РАСПИСАНИЯ
# ==========================================
async def check_schedule_task(app: Application):
    """Фоновая задача для проверки расписания"""
    print('🤖 Бот запущен! Мониторинг расписания...')
    
    # Отправляем приветствие
    try:
        keyboard = [
            [InlineKeyboardButton("📅 Показать расписание", callback_data='show_schedule')],
            [InlineKeyboardButton("🔄 Проверить изменения", callback_data='check_now')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await app.bot.send_message(
            chat_id=CHAT_ID,
            text='✅ *Бот запущен\\!*\nМониторинг расписания активен\\.',
            reply_markup=reply_markup,
            parse_mode='MarkdownV2'
        )
    except Exception as e:
        print(f'Ошибка отправки приветствия: {e}')
    
    while True:
        try:
            print(f'[{datetime.now().strftime("%H:%M:%S")}] Проверяю расписание...')
            
            current = get_column_ae()
            if not current:
                print('Не удалось получить данные')
                await asyncio.sleep(CHECK_INTERVAL)
                continue
            
            old = load_state()
            
            if not old:
                save_state(current)
                print('Первый запуск — состояние сохранено.')
            else:
                changes = find_changes(old, current)
                if changes:
                    print(f'Найдено изменений: {len(changes)}')
                    
                    msg = f'📅 *Расписание изменилось\\!*\n'
                    msg += f'🕐 {datetime.now().strftime("%d\\.%m\\.%Y %H:%M")}\n'
                    msg += f'━━━━━━━━━━━━━━━━━━\n\n'
                    
                    for c in changes[:10]:  # Первые 10 изменений
                        msg += f'📌 *Строка {c["row"]}*\n'
                        msg += f'  ❌ Было: `{escape_md(c["old_val"])}`\n'
                        msg += f'  ✅ Стало: `{escape_md(c["new_val"])}`\n\n'
                    
                    msg += f'━━━━━━━━━━━━━━━━━━\n'
                    msg += f'📊 Всего изменений: *{len(changes)}*'
                    
                    keyboard = [[InlineKeyboardButton("📅 Показать всё расписание", callback_data='show_schedule')]]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    
                    await app.bot.send_message(
                        chat_id=CHAT_ID,
                        text=msg,
                        reply_markup=reply_markup,
                        parse_mode='MarkdownV2'
                    )
                    
                    save_state(current)
                else:
                    print('Изменений нет.')
        
        except Exception as e:
            print(f'Ошибка в фоновой задаче: {e}')
        
        await asyncio.sleep(CHECK_INTERVAL)


# ==========================================
# ЗАПУСК БОТА
# ==========================================
async def main():
    # Создаём приложение с поддержкой прокси
    builder = ApplicationBuilder().token(BOT_TOKEN)
    
    if PROXY_URL:
        print(f'🔒 Используется прокси: {PROXY_URL}')
        builder.proxy(PROXY_URL).get_updates_proxy(PROXY_URL)
    
    app = builder.build()
    
    # Регистрируем обработчики
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CallbackQueryHandler(button_handler))
    
    # Запускаем фоновую задачу
    asyncio.create_task(check_schedule_task(app))
    
    # Запускаем бота
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    
    # Держим бота запущенным
    await asyncio.Event().wait()


if __name__ == '__main__':
    asyncio.run(main())
