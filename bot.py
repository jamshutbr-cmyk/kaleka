import os
import json
import asyncio
import pandas as pd
from datetime import datetime
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, ApplicationBuilder, CommandHandler,
    CallbackQueryHandler, MessageHandler, ContextTypes, filters
)
import httpx

# Загрузка .env
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
PROXY_URL      = os.environ.get('PROXY_URL')
SPREADSHEET_ID = '1tHn2XnJVUYOK-PZFBRIrGvLWFun7gyh0'
SHEET_GID      = '285132150'
CHECK_INTERVAL = 300
USERS_FILE     = 'users.json'   # хранит {chat_id: {group, col_index}}
STATE_FILE     = 'state.json'   # хранит {col_index: [данные столбца]}

CSV_URL = f'https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/export?format=csv&gid={SHEET_GID}'

DAYS = ['понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота']
DAY_EMOJI = {
    'понедельник': '📘', 'вторник': '📗', 'среда': '📙',
    'четверг': '📕', 'пятница': '📓', 'суббота': '📔'
}

# Столбцы групп: C=2 ... AQ=42
GROUP_COL_START = 2   # столбец C (0-индекс)
GROUP_COL_END   = 42  # столбец AQ (0-индекс)


# ==========================================
# РАБОТА С ПОЛЬЗОВАТЕЛЯМИ
# ==========================================
def load_users() -> dict:
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def save_users(users: dict):
    with open(USERS_FILE, 'w', encoding='utf-8') as f:
        json.dump(users, f, ensure_ascii=False, indent=2)


def get_user(chat_id: int) -> dict | None:
    users = load_users()
    return users.get(str(chat_id))


def set_user_group(chat_id: int, group: str, col_index: int):
    users = load_users()
    users[str(chat_id)] = {'group': group, 'col_index': col_index}
    save_users(users)


# ==========================================
# РАБОТА С ТАБЛИЦЕЙ
# ==========================================
def load_df():
    return pd.read_csv(CSV_URL, header=None)


def get_groups(df=None) -> dict:
    """Возвращает {название_группы: индекс_столбца}"""
    try:
        if df is None:
            df = load_df()
        groups = {}
        for col_idx in range(GROUP_COL_START, min(GROUP_COL_END + 1, df.shape[1])):
            # Ищем название группы в первых 10 строках
            for row_idx in range(10):
                val = str(df.iloc[row_idx, col_idx]).strip() if pd.notna(df.iloc[row_idx, col_idx]) else ''
                if val and val != 'nan' and any(c.isdigit() or c.isalpha() for c in val):
                    groups[val] = col_idx
                    break
        return groups
    except Exception as e:
        print(f'Ошибка при получении групп: {e}')
        return {}


def find_group(query: str, df=None) -> tuple[str, int] | None:
    """Ищет группу по введённому тексту, возвращает (название, индекс) или None"""
    groups = get_groups(df)
    query_clean = query.strip().upper()
    # Точное совпадение
    for name, idx in groups.items():
        if name.upper() == query_clean:
            return name, idx
    # Частичное совпадение
    matches = [(name, idx) for name, idx in groups.items() if query_clean in name.upper()]
    if len(matches) == 1:
        return matches[0]
    return None


def get_column(col_index: int, df=None) -> list:
    """Возвращает данные столбца группы"""
    try:
        if df is None:
            df = load_df()
        col = df.iloc[:, col_index].fillna('').astype(str).tolist()
        while col and not col[-1].strip():
            col.pop()
        return col
    except Exception as e:
        print(f'Ошибка при чтении столбца: {e}')
        return []


def get_schedule_for_col(col_index: int, df=None) -> dict:
    """Возвращает расписание для конкретного столбца"""
    try:
        if df is None:
            df = load_df()
        schedule = {}
        current_day = None
        for i in range(len(df)):
            col_a = str(df.iloc[i, 0]).strip() if pd.notna(df.iloc[i, 0]) else ''
            col_b = str(df.iloc[i, 1]).strip() if pd.notna(df.iloc[i, 1]) else ''
            col_val = str(df.iloc[i, col_index]).strip() if pd.notna(df.iloc[i, col_index]) else ''

            if col_a.lower() in DAYS:
                current_day = col_a.lower()
                schedule[current_day] = {}
            if col_b.isdigit() and current_day:
                schedule[current_day][col_b] = col_val
        return schedule
    except Exception as e:
        print(f'Ошибка при чтении расписания: {e}')
        return {}


def find_changes_smart(old: list, new: list, col_index: int, df=None) -> list:
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
                    'day':     current_day or '?',
                    'pair':    current_pair or '?',
                    'old_val': old_val or '(пусто)',
                    'new_val': new_val or '(пусто)'
                })
        return changes
    except Exception as e:
        print(f'Ошибка при поиске изменений: {e}')
        return []


# ==========================================
# СОСТОЯНИЕ (для каждого столбца отдельно)
# ==========================================
def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def save_state(state: dict):
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False)


# ==========================================
# ФОРМАТИРОВАНИЕ
# ==========================================
def escape_md(text: str) -> str:
    special = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    for ch in special:
        text = text.replace(ch, f'\\{ch}')
    return text


def format_schedule(schedule: dict, group: str) -> str:
    if not schedule:
        return '📭 Расписание пусто'

    msg = f'📅 *Расписание группы {escape_md(group)}:*\n\n'

    for day, pairs in schedule.items():
        emoji = DAY_EMOJI.get(day.lower(), '📖')
        msg += f'{emoji} *{escape_md(day.capitalize())}*\n'
        if pairs:
            for pair_num, subject in sorted(pairs.items(), key=lambda x: int(x[0])):
                if subject and subject != 'nan':
                    msg += f'  {pair_num}\\. `{escape_md(subject)}`\n'
                else:
                    msg += f'  {pair_num}\\. _\\(нет пары\\)_\n'
        else:
            msg += '  _\\(нет пар\\)_\n'
        msg += '\n'

    return msg


def format_changes(changes: list, group: str) -> str:
    msg  = f'🔔 *Изменение в расписании группы {escape_md(group)}\\!*\n'
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


def main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📅 Моё расписание",       callback_data='show_schedule')],
        [InlineKeyboardButton("🔄 Проверить изменения",  callback_data='check_now')],
        [InlineKeyboardButton("⚙️ Сменить группу",       callback_data='change_group')],
    ])


# ==========================================
# КОМАНДЫ БОТА
# ==========================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = get_user(chat_id)

    if user:
        # Уже зарегистрирован
        await update.message.reply_text(
            f'👋 *Привет\\!*\n\nТвоя группа: *{escape_md(user["group"])}*\nВыбери действие:',
            reply_markup=main_keyboard(),
            parse_mode='MarkdownV2'
        )
    else:
        # Первый запуск — просим ввести группу
        context.user_data['awaiting_group'] = True
        await update.message.reply_text(
            '👋 *Привет\\!*\n\n'
            'Я бот для мониторинга расписания колледжа\\.\n\n'
            '📝 Введи номер своей группы:\n'
            '_Например: 163Ф\\-9_',
            parse_mode='MarkdownV2'
        )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает текстовый ввод группы"""
    chat_id = update.effective_chat.id

    if not context.user_data.get('awaiting_group'):
        return

    query = update.message.text.strip()
    await update.message.reply_text('🔍 Ищу группу\\.\\.\\.', parse_mode='MarkdownV2')

    try:
        df = load_df()
        result = find_group(query, df)

        if result:
            group_name, col_idx = result
            set_user_group(chat_id, group_name, col_idx)
            context.user_data['awaiting_group'] = False

            await update.message.reply_text(
                f'✅ *Группа найдена\\!*\n\n'
                f'Твоя группа: *{escape_md(group_name)}*\n'
                f'Буду уведомлять об изменениях в расписании\\.',
                reply_markup=main_keyboard(),
                parse_mode='MarkdownV2'
            )
        else:
            # Показываем похожие группы
            groups = get_groups(df)
            query_up = query.upper()
            similar = [name for name in groups if query_up in name.upper()][:10]

            if similar:
                buttons = [[InlineKeyboardButton(g, callback_data=f'select_group_{g}')] for g in similar]
                await update.message.reply_text(
                    f'❓ Группа *{escape_md(query)}* не найдена\\.\n\nПохожие группы:',
                    reply_markup=InlineKeyboardMarkup(buttons),
                    parse_mode='MarkdownV2'
                )
            else:
                await update.message.reply_text(
                    f'❌ Группа *{escape_md(query)}* не найдена\\.\n\nПопробуй ещё раз:',
                    parse_mode='MarkdownV2'
                )
    except Exception as e:
        print(f'Ошибка при поиске группы: {e}')
        await update.message.reply_text('⚠️ Ошибка при поиске\\. Попробуй позже\\.', parse_mode='MarkdownV2')


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id

    # Выбор группы из списка похожих
    if query.data.startswith('select_group_'):
        group_name = query.data.replace('select_group_', '')
        df = load_df()
        groups = get_groups(df)
        col_idx = groups.get(group_name)

        if col_idx is not None:
            set_user_group(chat_id, group_name, col_idx)
            context.user_data['awaiting_group'] = False
            await query.edit_message_text(
                f'✅ *Группа выбрана\\!*\n\n'
                f'Твоя группа: *{escape_md(group_name)}*\n'
                f'Буду уведомлять об изменениях в расписании\\.',
                reply_markup=main_keyboard(),
                parse_mode='MarkdownV2'
            )
        return

    # Проверяем что пользователь зарегистрирован
    user = get_user(chat_id)
    if not user:
        await query.edit_message_text(
            '⚠️ Сначала введи свою группу\\. Напиши /start',
            parse_mode='MarkdownV2'
        )
        return

    group = user['group']
    col_idx = user['col_index']

    if query.data == 'show_schedule':
        await query.edit_message_text('⏳ Загружаю расписание\\.\\.\\.', parse_mode='MarkdownV2')
        df = load_df()
        schedule = get_schedule_for_col(col_idx, df)
        msg = format_schedule(schedule, group)
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data='back')]]
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='MarkdownV2')

    elif query.data == 'check_now':
        await query.edit_message_text('⏳ Проверяю изменения\\.\\.\\.', parse_mode='MarkdownV2')
        df = load_df()
        current = get_column(col_idx, df)
        state = load_state()
        old = state.get(str(col_idx), [])

        if not old:
            state[str(col_idx)] = current
            save_state(state)
            msg = '✅ Состояние сохранено\\. Теперь буду отслеживать изменения\\!'
        else:
            changes = find_changes_smart(old, current, col_idx, df)
            if changes:
                msg = format_changes(changes, group)
                state[str(col_idx)] = current
                save_state(state)
            else:
                msg = '✅ Изменений не обнаружено\\!'

        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data='back')]]
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='MarkdownV2')

    elif query.data == 'change_group':
        context.user_data['awaiting_group'] = True
        await query.edit_message_text(
            f'⚙️ *Смена группы*\n\n'
            f'Текущая группа: *{escape_md(group)}*\n\n'
            f'Введи новый номер группы:',
            parse_mode='MarkdownV2'
        )

    elif query.data == 'back':
        await query.edit_message_text(
            f'👋 Твоя группа: *{escape_md(group)}*\nВыбери действие:',
            reply_markup=main_keyboard(),
            parse_mode='MarkdownV2'
        )


# ==========================================
# ФОНОВАЯ ПРОВЕРКА
# ==========================================
async def check_schedule_task(app: Application):
    print('🤖 Бот запущен! Мониторинг расписания...')
    await asyncio.sleep(5)  # Ждём пока бот инициализируется    while True:
        try:
            print(f'[{datetime.now().strftime("%H:%M:%S")}] Проверяю расписание...')

            users = load_users()
            if not users:
                await asyncio.sleep(CHECK_INTERVAL)
                continue

            df = load_df()
            state = load_state()

            # Собираем уникальные столбцы
            cols_to_check = {}
            for uid, udata in users.items():
                col_idx = udata['col_index']
                if str(col_idx) not in cols_to_check:
                    cols_to_check[str(col_idx)] = {
                        'col_index': col_idx,
                        'group': udata['group'],
                        'users': []
                    }
                cols_to_check[str(col_idx)]['users'].append(uid)

            for col_key, col_data in cols_to_check.items():
                col_idx = col_data['col_index']
                group = col_data['group']
                current = get_column(col_idx, df)
                old = state.get(col_key, [])

                if not old:
                    state[col_key] = current
                    continue

                changes = find_changes_smart(old, current, col_idx, df)
                if changes:
                    print(f'Группа {group}: найдено изменений {len(changes)}')
                    msg = format_changes(changes, group)
                    keyboard = [[InlineKeyboardButton("📅 Показать расписание", callback_data='show_schedule')]]

                    for uid in col_data['users']:
                        try:
                            await app.bot.send_message(
                                chat_id=int(uid),
                                text=msg,
                                reply_markup=InlineKeyboardMarkup(keyboard),
                                parse_mode='MarkdownV2'
                            )
                        except Exception as e:
                            print(f'Ошибка отправки пользователю {uid}: {e}')

                    state[col_key] = current

            save_state(state)

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
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    asyncio.create_task(check_schedule_task(app))

    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    await asyncio.Event().wait()


if __name__ == '__main__':
    asyncio.run(main())
