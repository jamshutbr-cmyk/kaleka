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
    """Скачивает таблицу и возвращает столбец AE"""
    try:
        df = pd.read_csv(CSV_URL, header=None)
        if df.shape[1] > 30:
            col = df.iloc[:, 30].fillna('').astype(str).tolist()
            # Убираем пустые строки в конце
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


def find_changes(old: list, new: list) -> list:
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


def format_schedule(schedule: list) -> str:
    """Форматирует расписание для отображения"""
    if not schedule:
        return '📭 Расписание пусто'
    
    msg = '📅 *Текущее расписание:*\n\n'
    for i, item in enumerate(schedule, 1):
        if item.strip():
            msg += f'{i}\\. `{escape_md(item.strip())}`\n'
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
        schedule = get_column_ae()
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
        
        current = get_column_ae()
        old = load_state()
        
        if not old:
            save_state(current)
            msg = '✅ Состояние сохранено\\. Теперь буду отслеживать изменения\\!'
        else:
            changes = find_changes(old, current)
            if changes:
                msg = f'🔔 *Найдено изменений: {len(changes)}*\n\n'
                for c in changes[:5]:  # Показываем первые 5
                    msg += f'📌 Строка {c["row"]}\n'
                    msg += f'  ❌ Было: `{escape_md(c["old_val"])}`\n'
                    msg += f'  ✅ Стало: `{escape_md(c["new_val"])}`\n\n'
                save_state(current)
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
