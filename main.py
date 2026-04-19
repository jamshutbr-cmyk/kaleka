import os
import json
import time
import requests
import pandas as pd
from datetime import datetime

# ==========================================
# НАСТРОЙКИ
# ==========================================
BOT_TOKEN      = os.environ.get('BOT_TOKEN')
CHAT_ID        = os.environ.get('CHAT_ID')
SPREADSHEET_ID = '1tHn2XnJVUYOK-PZFBRIrGvLWFun7gyh0'
SHEET_GID      = '285132150'
CHECK_INTERVAL = 300  # проверка каждые 5 минут
STATE_FILE     = 'last_state.json'

# URL для скачивания листа как CSV
CSV_URL = f'https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/export?format=csv&gid={SHEET_GID}'


def get_column_ae() -> list:
    """Скачивает таблицу и возвращает столбец AE"""
    try:
        df = pd.read_csv(CSV_URL, header=None)
        # Столбец AE = индекс 30 (A=0, B=1, ... Z=25, AA=26, AB=27, AC=28, AD=29, AE=30)
        if df.shape[1] > 30:
            col = df.iloc[:, 30].fillna('').astype(str).tolist()
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


def build_message(changes: list) -> str:
    """Формирует красивое сообщение для Telegram"""
    now  = datetime.now()
    time_str = now.strftime('%d.%m.%Y %H:%M')

    msg  = f'📅 *Расписание изменилось\\!*\n'
    msg += f'🕐 {time_str}\n'
    msg += f'━━━━━━━━━━━━━━━━━━\n\n'

    for c in changes:
        msg += f'📌 *Строка {c["row"]}*\n'
        msg += f'  ❌ Было: `{escape_md(c["old_val"])}`\n'
        msg += f'  ✅ Стало: `{escape_md(c["new_val"])}`\n\n'

    msg += f'━━━━━━━━━━━━━━━━━━\n'
    msg += f'📊 Всего изменений: *{len(changes)}*'
    return msg


def escape_md(text: str) -> str:
    """Экранирует спецсимволы для MarkdownV2"""
    special = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    for ch in special:
        text = text.replace(ch, f'\\{ch}')
    return text


def send_telegram(text: str):
    """Отправляет сообщение в Telegram"""
    url = f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage'
    payload = {
        'chat_id':    CHAT_ID,
        'text':       text,
        'parse_mode': 'MarkdownV2'
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if not resp.ok:
            print(f'Ошибка Telegram: {resp.text}')
    except Exception as e:
        print(f'Ошибка отправки: {e}')


def main():
    print('🤖 Бот запущен! Мониторинг расписания...')
    send_telegram('✅ *Бот запущен\\!*\nМониторинг расписания активен\\. Буду уведомлять об изменениях в столбце AE\\.')

    while True:
        print(f'[{datetime.now().strftime("%H:%M:%S")}] Проверяю расписание...')

        current = get_column_ae()
        if not current:
            print('Не удалось получить данные, пропускаю...')
            time.sleep(CHECK_INTERVAL)
            continue

        old = load_state()

        if not old:
            # Первый запуск — просто сохраняем
            save_state(current)
            print('Первый запуск — состояние сохранено.')
        else:
            changes = find_changes(old, current)
            if changes:
                print(f'Найдено изменений: {len(changes)}')
                msg = build_message(changes)
                send_telegram(msg)
                save_state(current)
            else:
                print('Изменений нет.')

        time.sleep(CHECK_INTERVAL)


if __name__ == '__main__':
    main()
