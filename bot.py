import os
import json
import asyncio
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
PROXY_URL      = os.environ.get('PROXY_URL')
SPREADSHEET_ID = '1tHn2XnJVUYOK-PZFBRIrGvLWFun7gyh0'
SHEET_GID      = '285132150'
CHECK_INTERVAL = 300
STATE_FILE     = 'last_state.json'

CSV_URL = f'https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/export?format=csv&gid={SHEET_GID}'

DAYS = ['понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота']
DAY_EMOJI = {
    'понедельник': '📘',
    'вторник': '📗',
    'среда': '📙',
    'четверг': '📕',
    'пятница': '📓',
    'суббота': '📔'
}


# ==========================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ==========================================
def escape_md(text: str) -> str:
    special = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    for ch in special:
        text = text.replace(ch, f'\\{ch}')
    return text


def load_df():
    """Загружает таблицу"""
    return pd.read_csv(CSV_URL, header=None)


def get_column_ae(df=None) -> list:
    """Возвращает столбец AE как список"""
    try:
        if df is None:
            df = load_df()
        if df.shape[1] > 30:
            col = df.iloc[:, 30].fillna('').astype(str).tolist()
            while col and not col[-1].strip():
                col.pop()
            return col
        return []
    except Exception as e:
        print(f'Ошибка при чтении таблицы: {e}')
        return []


def get_full_schedule(df=None) -> dict:
    """Возвращает расписание структурированно по дням и парам"""
    try:
        if df is None:
            df = load_df()

        schedule = {}
        current_day = None

        for i in range(len(df)):
            col_a = str(df.iloc[i, 0]).strip() if pd.notna(df.iloc[i, 0]) else ''
            col_b = str(df.iloc[i, 1]).strip() if pd.notna(df.iloc[i, 1]) else ''
            col_ae = str(df.iloc[i, 30]).strip() if df.shape[1] > 30 and pd.notna(df.iloc[i, 30]) else ''

            if col_a.lower() in DAYS:
                current_day = col_a.lower()
                schedule[current_day] = {}

            if col_b.isdigit() and current_day:
                schedule[current_day][col_b] = col_ae

        return schedule
    except Exception as e:
        print(f'Ошибка при чтении расписания: {e}')
        return {}


def find_changes_smart(old: list, new: list, df=None) -> list:
    """Находит изменения с контекстом — день и пара"""
    try:
        if df is None:
            df = load_df()

        changes = []
        current_day = None
        current_pair = None
        max_len = max(len(old), len(new))

        for i in range(max_len):
            if i < len(df):
                col_a = str(df.iloc[i, 0]).strip() if pd.notna(df.iloc[i, 0]) else ''
                col_b = str(df.iloc[i, 1]).strip() if pd.notna(df.iloc[i, 1]) else ''

                if col_a.lower() in DAYS:
                    current_day = col_a.capitalize()
                if col_b.isdigit():
                    current_pair = col_b

            old_val = old[i].strip() if i < len(old) else ''
            new_val = new[i].strip() if i < len(new) else ''

            if old_val != new_val:
                changes.append({
                    'day':     current_day or 'Неизвестный день',
                    'pair':    current_pair or '?',
                    'old_val': old_val or '(пусто)',
                    'new_val': new_val or '(пусто)'
                })

        return changes
    except Exception as e:
        print(f'Ошибка при поиске изменений: {e}')
        return []


def load_state() -> list:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []


def save_state(data: list):
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)


def format_schedule(schedule: dict) -> str:
    if not schedule:
        return '📭 Расписание пусто'

    msg = '📅 *Текущее расписание:*\n\n'

    for day, pairs in schedule.items():
        emoji = DAY_EMOJI.get(day.lower(), '📖')
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


def format_changes(changes: list) -> str:
    msg  = f'🔔 *Расписание изменилось\\!*\n'
    msg += f'🕐 {escape_md(datetime.now().strftime("%d.%m.%Y %H:%M"))}\n'
    msg += f'━━━━━━━━━━━━━━━━━━\n\n'

    for c in changes[:10]:
        emoji = DAY_EMOJI.get(c['day'].lower(), '📖')
        msg += f'{emoji} *{escape_md(c["day"])}, {c["pair"]} пара*\n'
        msg += f'  ❌ Было: `{escape_md(c["old_val"])}`\n'
        msg += f'  ✅ Стало: `{escape_md(c["new_val"])}`\n\n'

    if len(changes) > 10:
        msg += f'_\\.\\.\\. и ещё {len(changes) - 10} изменений_\n\n'

    msg += f'━━━━━━━━━━━━━━━━━━\n'
    msg += f'📊 Всего изменений: *{len(changes)}*'

    return msg


# ==========================================
# КОМАНДЫ БОТА
# ==========================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📅 Показать расписание", callback_data='show_schedule')],
        [InlineKeyboardButton("🔄 Проверить изменения",  callback_data='check_now')],
        [InlineKeyboardButton("ℹ️ Информация",           callback_data='info')]
    ]
    await update.message.reply_text(
        '👋 *Привет\\!*\n\nЯ бот для мониторинга расписания\\.\nВыбери действие:',
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='MarkdownV2'
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == 'show_schedule':
        schedule = get_full_schedule()
        msg = format_schedule(schedule)
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data='back')]]
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='MarkdownV2')

    elif query.data == 'check_now':
        await query.edit_message_text('⏳ Проверяю изменения\\.\\.\\.', parse_mode='MarkdownV2')

        df = load_df()
        current = get_column_ae(df)
        old = load_state()

        if not old:
            save_state(current)
            msg = '✅ Состояние сохранено\\. Теперь буду отслеживать изменения\\!'
        else:
            changes = find_changes_smart(old, current, df)
            if changes:
                msg = format_changes(changes)
                save_state(current)
            else:
                msg = '✅ Изменений не обнаружено\\!'

        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data='back')]]
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='MarkdownV2')

    elif query.data == 'info':
        msg = (
            'ℹ️ *Информация о боте*\n\n'
            '🔹 Проверяю расписание каждые 5 минут\n'
            '🔹 При изменении отправляю уведомление с указанием дня и пары\n'
            '🔹 Можно вручную проверить через кнопки\n\n'
            f'📊 Таблица: [Открыть](https://docs\\.google\\.com/spreadsheets/d/{SPREADSHEET_ID})\n'
            f'🕐 Интервал проверки: {CHECK_INTERVAL // 60} минут'
        )
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data='back')]]
        await query.edit_message_text(
            msg, reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='MarkdownV2', disable_web_page_preview=True
        )

    elif query.data == 'back':
        keyboard = [
            [InlineKeyboardButton("📅 Показать расписание", callback_data='show_schedule')],
            [InlineKeyboardButton("🔄 Проверить изменения",  callback_data='check_now')],
            [InlineKeyboardButton("ℹ️ Информация",           callback_data='info')]
        ]
        await query.edit_message_text(
            '👋 *Привет\\!*\n\nЯ бот для мониторинга расписания\\.\nВыбери действие:',
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='MarkdownV2'
        )


# ==========================================
# ФОНОВАЯ ПРОВЕРКА
# ==========================================
async def check_schedule_task(app: Application):
    print('🤖 Бот запущен! Мониторинг расписания...')

    try:
        keyboard = [
            [InlineKeyboardButton("📅 Показать расписание", callback_data='show_schedule')],
            [InlineKeyboardButton("🔄 Проверить изменения",  callback_data='check_now')]
        ]
        await app.bot.send_message(
            chat_id=CHAT_ID,
            text='✅ *Бот запущен\\!*\nМониторинг расписания активен\\.',
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='MarkdownV2'
        )
    except Exception as e:
        print(f'Ошибка отправки приветствия: {e}')

    while True:
        try:
            print(f'[{datetime.now().strftime("%H:%M:%S")}] Проверяю расписание...')

            df = load_df()
            current = get_column_ae(df)

            if not current:
                print('Не удалось получить данные')
                await asyncio.sleep(CHECK_INTERVAL)
                continue

            old = load_state()

            if not old:
                save_state(current)
                print('Первый запуск — состояние сохранено.')
            else:
                changes = find_changes_smart(old, current, df)
                if changes:
                    print(f'Найдено изменений: {len(changes)}')
                    msg = format_changes(changes)
                    keyboard = [[InlineKeyboardButton("📅 Показать всё расписание", callback_data='show_schedule')]]
                    await app.bot.send_message(
                        chat_id=CHAT_ID,
                        text=msg,
                        reply_markup=InlineKeyboardMarkup(keyboard),
                        parse_mode='MarkdownV2'
                    )
                    save_state(current)
                else:
                    print('Изменений нет.')

        except Exception as e:
            print(f'Ошибка в фоновой задаче: {e}')

        await asyncio.sleep(CHECK_INTERVAL)


# ==========================================
# ЗАПУСК
# ==========================================
async def main():
    builder = ApplicationBuilder().token(BOT_TOKEN)

    if PROXY_URL:
        print(f'🔒 Используется прокси: {PROXY_URL}')
        builder.proxy(PROXY_URL).get_updates_proxy(PROXY_URL)

    app = builder.build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CallbackQueryHandler(button_handler))

    asyncio.create_task(check_schedule_task(app))

    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    await asyncio.Event().wait()


if __name__ == '__main__':
    asyncio.run(main())
