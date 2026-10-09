import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:  # dotenv optional; plain env vars work too
    pass

# ============================================================
#  SOLANA GEM DEV SCANNER — КОНФИГ
# ─── ОСНОВНЫЕ НАСТРОЙКИ ──────────────────────────────────
# Сюда нужно вставить свои ключи
HELIUS_API_KEY = os.environ.get("HELIUS_API_KEY", "")

# ─── НАСТРОЙКИ СКРАПЕРА (Cloudflare Bypass) ──────────────
CAPSOLVER_API_KEY = os.environ.get("CAPSOLVER_API_KEY", "")
PROXY = os.environ.get("PROXY", "")

# --- Google Sheets ---
GOOGLE_SHEET_NAME      = "Gem Dev Scanner"    # Название твоей таблицы в Google
GOOGLE_CREDENTIALS_FILE = "credentials.json" # JSON файл от Google Service Account

# --- Скоринг ---
MIN_SCORE_FOR_SHEET = 6    # Минимальный балл для попадания в таблицу (из 10)

# --- Фильтры токенов ---
MAX_TOKEN_AGE_HOURS  = 48       # Не смотреть токены старше N часов
MAX_MARKET_CAP_USD   = 100_000  # Максимальный MC в $
TOP_HOLDERS_MAX_PCT = 50       # макс % у топ-10 холдеров (50% реалистично для pump.fun)
CHECK_LAST_N_TOKENS  = 5        # Сколько прошлых токенов дева анализировать

MIN_PROVEN_MC     = 500_000   # мин MC токена чтобы считать его "доказанным" ($500k)
MAX_PROVEN_MC     = 50_000_000 # верхний потолок (>$50M — слишком известен)
PROVEN_SCAN_LIMIT = 50         # сколько доказанных токенов проверять за запуск


# --- Задержки (сек) чтобы не получить бан ---
REQUEST_DELAY = 0.3
