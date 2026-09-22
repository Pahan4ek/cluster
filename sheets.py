"""
sheets.py — запись КОШЕЛЬКОВ проверенных девов в Google Sheets

Таблица = постоянный вотчлист для снайпинга.
Один дев = одна строка. Дубликаты не пишутся.
"""

import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime, timezone
from config import GOOGLE_SHEET_NAME, GOOGLE_CREDENTIALS_FILE

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

HEADERS = [
    "📅 Добавлен",
    "🏆 Грейд",
    "🎯 Скор",
    "👤 Кошелёк дева",
    "🥇 Лучший токен",
    "💰 Лучший MC",
    "📋 Все токены",
    "✅ Успешных",
    "📈 Мигрировали",   # новая
    "📊 Всего запусков",   # новая
    "🎯 % успеха",          # новая
    "💬 Заметки",
    "🔍 Solscan",
    "📈 Cielo PnL",
]


def _get_sheet():
    creds  = Credentials.from_service_account_file(GOOGLE_CREDENTIALS_FILE, scopes=SCOPES)
    client = gspread.authorize(creds)
    return client.open(GOOGLE_SHEET_NAME).sheet1


def init_sheet():
    """Создать/обновить шапку таблицы."""
    sheet = _get_sheet()
    existing = sheet.row_values(1)
    if not existing or existing[0] != HEADERS[0]:
        sheet.clear()
        sheet.insert_row(HEADERS, index=1)
        sheet.format("A1:N1", {
            "textFormat":          {"bold": True, "fontSize": 10},
            "backgroundColor":     {"red": 0.07, "green": 0.07, "blue": 0.12},
            "horizontalAlignment": "CENTER",
        })
        sheet.freeze(rows=1)
        print("[OK] Шапка таблицы создана")
    else:
        print("[OK] Шапка уже существует")
    return sheet


def _wallet_exists(sheet, wallet: str) -> bool:
    """Проверить — есть ли уже этот кошелёк в таблице."""
    col = sheet.col_values(4)   # колонка "Кошелёк дева"
    return wallet in col


def write_dev_wallet(creator: str, row_data: dict) -> bool:
    """
    Записать кошелёк дева в таблицу.
    Возвращает True если записан, False если уже был.
    """
    sheet = _get_sheet()

    if _wallet_exists(sheet, creator):
        return False   # уже есть

    row = [
        row_data.get("added",      ""),
        row_data.get("grade",      ""),
        row_data.get("score",      ""),
        creator,
        row_data.get("best_token", ""),
        row_data.get("best_mc",    ""),
        row_data.get("all_tokens", ""),
        row_data.get("n_success",  ""),
        row_data.get("n_migrated", ""),   # новое
        row_data.get("n_total",    ""),   # новое
        row_data.get("success_pct",""),   # новое
        row_data.get("notes",      ""),
        row_data.get("solscan",    ""),
        row_data.get("cielo",      ""),
    ]

    sheet.append_row(row, value_input_option="USER_ENTERED")

    grade = row_data.get("grade", "")
    score = row_data.get("score", "")
    best  = row_data.get("best_token", "")
    mc    = row_data.get("best_mc", "")
    print(f"      [+] {grade} {score} | {best} {mc} → записан")
    return True


# ─── Обратная совместимость со старым кодом ───────────────

def write_dev_to_sheet(token_info, score_data, past_tokens):
    """Устаревший метод — используй write_dev_wallet."""
    pass
