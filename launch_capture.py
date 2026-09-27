# -*- coding: utf-8 -*-
"""
launch_capture.py — перехват первых покупателей нового токена на pump.fun

Зачем:
  collector.py копит историю девов (кто что запускал, что дошло до Raydium).
  Этот модуль решает другую задачу: для КАЖДОГО нового запуска собрать
  список первых покупателей с таймингами — сырьё для кластерного анализа.

Как:
  1. WS PumpPortal: subscribeNewToken -> узнаём о новом минте (бесплатный метод)
  2. Через WINDOW_SEC читаем первые транзакции токена прямо из цепочки
     через Helius RPC: getSignaturesForAddress по минту -> getTransaction
  3. Пишем в SQLite

ПОЧЕМУ НЕ ЖИВАЯ ПОДПИСКА НА СДЕЛКИ (осознанное решение, не недоделка):

  а) subscribeTokenTrade у PumpPortal платный: 0.01 SOL за 10k событий,
     ключ привязан к кошельку, который они же и создают. При ~1100 новых
     токенов в час это порядка 0.5-1 SOL в сутки.

  б) И главное — он всё равно не решает задачу. О токене мы узнаём из
     события создания, которое приходит УЖЕ ПОСЛЕ слота 0. Подписка,
     оформленная в этот момент, бандл не увидит: он произошёл раньше.
     Первый блок в любом случае приходится восстанавливать из цепочки.

  Поэтому источник истины — сама цепочка, а не чужой фид. Побочная
  выгода: нет зависимости от вендора, который завтра поменяет цены
  или закроет метод.

  Цена решения: вердикт получается за 5-15 секунд вместо долей секунды.
  У RugCheck снимок постфактум, у Bubblemaps минуты — запас остаётся.

Запуск:
    python launch_capture.py
Останавливается по Ctrl+C.
"""

import sys
if sys.platform == "win32":
    # reconfigure, а не TextIOWrapper: повторный вызов безвреден.
    # Оборачивание дважды (свой + импортированный модуль) закрывало буфер.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import asyncio
import json
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import websockets

from rpc import rpc, rpc_batch, check_key, HELIUS_API_KEY
from parsing import parse_pumpfun_trade

# ─── настройки ────────────────────────────────────────────
WS_URI       = "wss://pumpportal.fun/api/data"
HELIUS_RPC   = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
DB_FILE      = Path(__file__).parent / "launches.db"

WINDOW_SEC   = 60     # сколько секунд слушаем сделки нового токена
MAX_BACKFILL = 15     # первых транзакций на токен: бандл лежит в самом начале,
                      # глубже смотреть незачем, а каждая единица тут = кредит на запуск
BATCH_SIZE   = 25     # столько getTransaction уходит ОДНИМ POST (JSON-RPC batch)
MAX_PARALLEL = 2      # одновременных финализаций
MIN_INTERVAL = 0.20   # минимум секунд между запросами, общий на весь процесс
RPC_TIMEOUT  = 20


# ─── хранилище ────────────────────────────────────────────
SCHEMA = """
CREATE TABLE IF NOT EXISTS launches (
    mint         TEXT PRIMARY KEY,
    creator      TEXT,
    name         TEXT,
    symbol       TEXT,
    detected_ts  INTEGER,
    finalized_ts INTEGER,
    ws_trades    INTEGER DEFAULT 0,
    rpc_trades   INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS trades (
    signature    TEXT PRIMARY KEY,
    mint         TEXT,
    trader       TEXT,
    tx_type      TEXT,
    sol_amount   REAL,
    token_amount REAL,
    ts           INTEGER,
    slot         INTEGER,
    source       TEXT          -- 'ws' | 'rpc'
);
CREATE INDEX IF NOT EXISTS idx_trades_mint ON trades(mint);
CREATE INDEX IF NOT EXISTS idx_trades_trader ON trades(trader);
"""


def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def hhmmss() -> str:
    return datetime.now().strftime("%H:%M:%S")


# RPC-слой вынесен в rpc.py: throttle и батчинг общие для всех скриптов


def fetch_earliest_txs(mint: str, limit: int = MAX_BACKFILL) -> list[dict]:
    """Самые ранние транзакции по минту — там лежит слот 0."""
    sigs = rpc("getSignaturesForAddress", [mint, {"limit": 1000}])
    if not sigs:
        return []

    oldest = [e["signature"] for e in reversed(sigs) if e.get("signature")][:limit]
    if not oldest:
        return []

    cfg = {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}
    txs = rpc_batch("getTransaction", [[sig, cfg] for sig in oldest])

    out = []
    for sig, tx in zip(oldest, txs):
        if not tx:
            continue
        parsed = parse_pumpfun_trade(tx, mint)
        if parsed:
            parsed["signature"] = sig
            parsed["slot"] = tx.get("slot")
            parsed["ts"] = (tx.get("blockTime") or 0) * 1000
            parsed["source"] = "rpc"
            out.append(parsed)
    return out


# ─── состояние наблюдения ─────────────────────────────────
class Watcher:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.active: dict[str, dict] = {}   # mint -> {meta, trades[]}

    def start(self, mint, creator, name, symbol):
        if mint in self.active:
            return
        self.active[mint] = {
            "creator": creator, "name": name, "symbol": symbol,
            "detected_ts": now_ms(), "trades": [], "deadline": time.time() + WINDOW_SEC,
        }
        self.conn.execute(
            "INSERT OR IGNORE INTO launches (mint, creator, name, symbol, detected_ts) VALUES (?,?,?,?,?)",
            (mint, creator, name, symbol, now_ms()),
        )
        self.conn.commit()

    def add_trade(self, mint, data):
        w = self.active.get(mint)
        if not w:
            return
        w["trades"].append({
            "signature":    data.get("signature"),
            "mint":         mint,
            "trader":       data.get("traderPublicKey"),
            "tx_type":      data.get("txType"),
            "sol_amount":   data.get("solAmount"),
            "token_amount": data.get("tokenAmount"),
            "ts":           now_ms(),
            "slot":         None,
            "source":       "ws",
        })

    def due(self) -> list[str]:
        """Минты с истёкшим окном. Помечаем сразу, иначе пока finalize ждёт
        ответа RPC, цикл успеет выдать тот же минт ещё несколько раз."""
        now = time.time()
        out = []
        for m, w in self.active.items():
            if now >= w["deadline"] and not w.get("finalizing"):
                w["finalizing"] = True
                out.append(m)
        return out

    def save(self, mint, rpc_trades: list[dict]):
        w = self.active.pop(mint, None)
        if w is None:
            return 0, 0

        rows = w["trades"] + rpc_trades
        saved = 0
        for t in rows:
            if not t.get("signature") or not t.get("trader"):
                continue
            try:
                self.conn.execute(
                    """INSERT OR IGNORE INTO trades
                       (signature, mint, trader, tx_type, sol_amount, token_amount, ts, slot, source)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (t["signature"], t["mint"], t["trader"], t.get("tx_type"),
                     t.get("sol_amount"), t.get("token_amount"), t.get("ts"),
                     t.get("slot"), t.get("source")),
                )
                saved += 1
            except Exception as e:
                print(f"    [DB ERR] {e}")

        self.conn.execute(
            "UPDATE launches SET finalized_ts=?, ws_trades=?, rpc_trades=? WHERE mint=?",
            (now_ms(), len(w["trades"]), len(rpc_trades), mint),
        )
        self.conn.commit()
        return len(w["trades"]), len(rpc_trades)


# ─── основной цикл ────────────────────────────────────────
_sem = None  # создаётся в run(), т.к. нужен живой event loop


async def finalize(watcher: Watcher, mint: str):
    """Добор через Helius + сохранение + отписка."""
    meta = watcher.active.get(mint, {})
    symbol = meta.get("symbol", "???")

    try:
        async with _sem:
            rpc_trades = await asyncio.to_thread(fetch_earliest_txs, mint)
    except KeyError:
        print("[FATAL] Helius 401 — добор через RPC отключён, проверь ключ в .env")
        rpc_trades = []
    n_ws, n_rpc = watcher.save(mint, rpc_trades)

    uniq = {t["trader"] for t in rpc_trades if t.get("trader")}
    print(f"[{hhmmss()}] DONE   {symbol:12s} | ws:{n_ws:3d} rpc:{n_rpc:3d} | уникальных ранних кошельков: {len(uniq)}")


async def run():
    global _sem
    _sem = asyncio.Semaphore(MAX_PARALLEL)

    if not check_key():
        return

    conn = db_connect()
    watcher = Watcher(conn)

    print("=" * 64)
    print("  LAUNCH CAPTURE — первые покупатели новых токенов")
    print(f"  окно наблюдения: {WINDOW_SEC}s | добор через Helius: {MAX_BACKFILL} tx")
    print(f"  база: {DB_FILE.name} | Ctrl+C для остановки")
    print("=" * 64)

    while True:
        try:
            async with websockets.connect(WS_URI, ping_interval=30) as ws:
                await ws.send(json.dumps({"method": "subscribeNewToken"}))
                print("[OK] подписан на новые токены\n")

                while True:
                    # обслуживаем истёкшие окна
                    for mint in watcher.due():
                        asyncio.create_task(finalize(watcher, mint))

                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue

                    try:
                        data = json.loads(raw)
                    except Exception:
                        continue

                    tx_type = data.get("txType", "")
                    mint    = data.get("mint", "")

                    if tx_type == "create" and mint:
                        creator = data.get("traderPublicKey") or data.get("creator", "")
                        symbol  = data.get("symbol", "")
                        watcher.start(mint, creator, data.get("name", ""), symbol)
                        print(f"[{hhmmss()}] WATCH  {symbol:12s} | {mint[:10]}… | в работе: {len(watcher.active)}")

                    elif tx_type in ("buy", "sell") and mint in watcher.active:
                        watcher.add_trade(mint, data)

        except websockets.exceptions.ConnectionClosed:
            print("[!] соединение потеряно, переподключаюсь через 5с…")
            await asyncio.sleep(5)
        except Exception as e:
            print(f"[ERR] {e} — переподключаюсь через 5с…")
            await asyncio.sleep(5)


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\n[STOP] остановлено")
