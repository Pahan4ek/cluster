# 💣 Solana Gem Dev Scanner

Автоматически находит перспективных девелоперов токенов на Solana и записывает их в Google Sheets.

## Быстрый старт

### 1. Установи зависимости
```bash
pip install -r requirements.txt
```

### 2. Получи Helius API Key
1. Зайди на [helius.dev](https://helius.dev)
2. Зарегистрируйся (бесплатно)
3. Скопируй API ключ
4. Вставь в `config.py` → `HELIUS_API_KEY`

### 3. Настрой Google Sheets

#### Создай Service Account:
1. Зайди в [Google Cloud Console](https://console.cloud.google.com)
2. Создай новый проект (или выбери существующий)
3. Включи **Google Sheets API** и **Google Drive API**
4. Создай Service Account → **IAM & Admin → Service Accounts**
5. Создай JSON ключ → скачай как `credentials.json`
6. Положи `credentials.json` в папку со скриптом

#### Настрой таблицу:
1. Создай новую Google Таблицу с названием **"Gem Dev Scanner"** (или своим — укажи в config.py)
2. Нажми **"Поделиться"** → введи email сервис-аккаунта из `credentials.json` (поле `client_email`) → **Редактор**

### 4. Создай шапку таблицы (один раз)
```bash
python scanner.py --init
```

### 5. Запускай сканер
```bash
# Обычный запуск
python scanner.py

# Тест без записи в таблицу
python scanner.py --dry-run
```

---

## Структура файлов

```
solana-gem-scanner/
├── scanner.py        ← запускаешь это
├── fetcher.py        ← API запросы
├── scorer.py         ← логика скоринга
├── sheets.py         ← запись в Google Sheets
├── config.py         ← настройки (заполни API ключи)
├── requirements.txt  ← зависимости
└── credentials.json  ← (добавь сам) Google Service Account
```

---

## Система скоринга (0–10 баллов)

| Критерий | Баллы |
|---|---|
| Прошлый токен дева дошёл до Raydium | +3 |
| Дев не продавал в первые 6 часов | +2 |
| Топ-10 холдеров < 25% supply | +2 |
| Есть Twitter + Telegram | +1 |
| Текущий MC < $100,000 | +1 |
| Токен < 24 часов | +1 |

| Скор | Категория |
|---|---|
| 8–10 | 🟢 ГЕМ |
| 6–7 | 🟡 WATCH |
| 0–5 | 🔴 SKIP (не записывается) |

---

## Настройки (config.py)

| Параметр | По умолчанию | Описание |
|---|---|---|
| `MIN_SCORE_FOR_SHEET` | 6 | Минимальный балл для таблицы |
| `MAX_TOKEN_AGE_HOURS` | 48 | Максимальный возраст токена |
| `MAX_MARKET_CAP_USD` | 100,000 | Максимальный MC в $ |
| `TOP_HOLDERS_MAX_PCT` | 25 | Макс % у топ-10 холдеров |
| `PUMP_SCAN_LIMIT` | 100 | Токенов за один запуск |
