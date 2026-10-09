# -*- coding: utf-8 -*-
"""
collector.py — Реальный сбор данных с pump.fun через PumpPortal WebSocket

Что делает:
  1. Подключается к wss://pumpportal.fun/api/data
  2. Подписывается на subscribeNewToken (новые токены)
  3. Подписывается на subscribeMigration (токены достигшие Raydium)
  4. Хранит историю девов локально в dev_history.json

Запуск (в отдельном окне):
  python collector.py

Останавливается по Ctrl+C. Накапливает базу — чем дольше работает,
тем точнее данные по каждому деву.
"""

import sys, io
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import asyncio
import json
import os
import signal
from datetime import datetime, timezone
from pathlib import Path

import websockets

# ─── Хранилище ───────────────────────────────────────────
DATA_FILE = Path(__file__).parent / "dev_history.json"
WS_URI    = "wss://pumpportal.fun/api/data"

# dev_history структура:
# {
#   "wallet_address": {
#     "total":     int,    # всего запущено токенов
#     "migrated":  int,    # дошли до Raydium
#     "tokens":    [       # последние 20 токенов
#       {"mint": str, "name": str, "symbol": str, "ts": int, "migrated": bool}
#     ]
#   }
# }


def load_history() -> dict:
    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_history(history: dict):
    DATA_FILE.write_text(
        json.dumps(history, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def handle_new_token(data: dict, history: dict):
    """Обработка нового токена с pump.fun."""
    creator = data.get("traderPublicKey") or data.get("creator", "")
    mint    = data.get("mint", "")
    name    = data.get("name", "")
    symbol  = data.get("symbol", "")

    if not creator or not mint:
        return

    if creator not in history:
        history[creator] = {"total": 0, "migrated": 0, "tokens": []}

    dev = history[creator]
    dev["total"] += 1

    # Добавляем токен (держим последние 50)
    token_entry = {
        "mint":     mint,
        "name":     name,
        "symbol":   symbol,
        "ts":       now_ms(),
        "migrated": False,
    }
    dev["tokens"].insert(0, token_entry)
    dev["tokens"] = dev["tokens"][:50]

    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] NEW    {symbol:12s} | dev: {creator[:12]}... | всего: {dev['total']}")


def handle_migration(data: dict, history: dict):
    """Обработка миграции токена на Raydium."""
    mint    = data.get("mint", "")
    creator = data.get("traderPublicKey") or data.get("creator", "")

    if not mint:
        return

    # Ищем этот mint в истории и помечаем как мигрировавший
    if not creator:
        # Ищем по mint во всех девах
        for dev_wallet, dev_data in history.items():
            for token in dev_data["tokens"]:
                if token.get("mint") == mint and not token.get("migrated"):
                    token["migrated"] = True
                    dev_data["migrated"] += 1
                    creator = dev_wallet
                    break
            if creator:
                break
    elif creator in history:
        for token in history[creator]["tokens"]:
            if token.get("mint") == mint and not token.get("migrated"):
                token["migrated"] = True
                history[creator]["migrated"] += 1
                break

    migrated_count = history.get(creator, {}).get("migrated", 0)
    total_count    = history.get(creator, {}).get("total", 1)
    symbol = next(
        (t["symbol"] for t in history.get(creator, {}).get("tokens", []) if t.get("mint") == mint),
        "???"
    )

    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] MIGR   {symbol:12s} -> Raydium | dev: {creator[:12] if creator else '?'}... | migr: {migrated_count}/{total_count}")


async def run_collector():
    """Основной цикл WebSocket."""
    history       = load_history()
    total_new     = 0
    total_migrated = 0
    last_save     = 0

    print("=" * 60)
    print("  [*] PUMPPORTAL COLLECTOR")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Девов в базе: {len(history)}")
    print("  Ctrl+C для остановки")
    print("=" * 60)

    while True:
        try:
            async with websockets.connect(WS_URI, ping_interval=30) as ws:
                # Подписываемся на новые токены
                await ws.send(json.dumps({"method": "subscribeNewToken"}))
                print("[OK] Подписан на новые токены")

                # Подписываемся на миграции
                await ws.send(json.dumps({"method": "subscribeMigration"}))
                print("[OK] Подписан на миграции")
                print("[>>] Ожидаю события...\n")

                async for raw in ws:
                    try:
                        data = json.loads(raw)
                    except Exception:
                        continue

                    # Определяем тип события
                    tx_type = data.get("txType", "")
                    event   = data.get("event", "")

                    if tx_type == "create" or event == "newToken":
                        handle_new_token(data, history)
                        total_new += 1

                    elif tx_type == "migration" or event == "migration" or data.get("pool"):
                        handle_migration(data, history)
                        total_migrated += 1

                    # Сохраняем каждые 50 событий
                    total_events = total_new + total_migrated
                    if total_events - last_save >= 50:
                        save_history(history)
                        last_save = total_events
                        print(f"  [SAVE] база: {len(history)} девов | новых: {total_new} | мигр: {total_migrated}")

        except websockets.exceptions.ConnectionClosed:
            print("[!] Соединение потеряно, переподключаюсь через 5с...")
            save_history(history)
            await asyncio.sleep(5)

        except Exception as e:
            print(f"[ERR] {e} — переподключаюсь через 5с...")
            save_history(history)
            await asyncio.sleep(5)


def main():
    history = load_history()

    def on_exit(*args):
        print("\n[STOP] Сохраняю базу...")
        save_history(history)
        sys.exit(0)

    signal.signal(signal.SIGINT, on_exit)

    try:
        asyncio.run(run_collector())
    except KeyboardInterrupt:
        print("\n[STOP] Сохраняю базу...")
        save_history(history)


if __name__ == "__main__":
    main()
