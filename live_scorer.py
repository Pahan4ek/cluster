# -*- coding: utf-8 -*-
"""
live_scorer.py — живой скорер ClusterPulse

Поток: WebSocket logsSubscribe на программу pump (commitment confirmed).
Из логов каждой транзакции достаём события CreateEvent / TradeEvent /
CompleteEvent. Это бесплатно по RPC-кредитам: ни одного getTransaction
на запуск. Внешняя программа (прямой вызов pump или роутер терминала)
берётся из того же лога.

Через DECIDE_AFTER секунд после создания запуска считаем признаки первых
10 секунд (ровно как в retro/census.sql), прогоняем модель
model/live_model.json и пишем результат в live.db. Красный уровень —
в консоль и, если заданы TG_BOT_TOKEN и TG_CHAT_ID, в Telegram.
Через 5 минут — второй этап: сколько ранних покупателей и через какие
программы зашли. Когда запуск затих — пишем исход (для своей разметки).

Подготовка (один раз):
    python -m live.devdb --seed        # история девов из dune_out/census_*

Запуск:
    python live_scorer.py                      # живой поток
    python live_scorer.py --record raw.jsonl   # и писать сырые сообщения (первые 20k, см. --record-max)
    python live_scorer.py --replay raw.jsonl   # прогнать записанное, без сети

Источник данных (--source, по умолчанию auto):
    mirage — Solami Mirage: Yellowstone geyser поверх WebSocket, транзакции целиком,
             события pump из внутренних инструкций, без добора getTransaction.
             Нужен SOLAMI_API_KEY (MirageStream + MirageManage) или SOLAMI_MIRAGE_ID.
    ws     — logsSubscribe: WS_URL из .env, иначе Solami (SOLAMI_API_KEY), иначе Helius.
    auto   — mirage, если есть SOLAMI_API_KEY, иначе ws.
    python live_scorer.py --source mirage --record raw_mirage.jsonl   # писать кадры Mirage (base64)
"""
import argparse
import asyncio
import json
import os
import queue
import sqlite3
import sys
import threading
import time
from collections import deque
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from live import mirage
from live.devdb import DevDB
from live.model import Model
from live.pump_events import PUMP_PROGRAM, SOL_QUOTE, events_from_logs, events_from_tx
from live.state import Launch, TickerWindow

DECIDE_AFTER = float(os.environ.get("DECIDE_AFTER", 12))     # с после создания: 10 с окна + запас на confirmed
STAGE2_AFTER = 305
EVICT_SILENT = 3600          # запуск без сделок час -> пишем исход, выкидываем из памяти
EVICT_AGE = 86400
PENDING_TTL = 15             # сделки по минту, чьё создание ещё не пришло
MIN_WALLETS_ALERT = int(os.environ.get("MIN_WALLETS_ALERT", 3))
LIVE_DB = ROOT / "live.db"
MODEL_PATH = ROOT / os.environ.get("MODEL_PATH", "model/live_model.json")
ALERT_LANG = os.environ.get("ALERT_LANG", "en")   # язык алертов в Telegram: en | ru
DEV_DB = ROOT / "devdb.sqlite"

SCHEMA = """
CREATE TABLE IF NOT EXISTS scores (
    mint TEXT PRIMARY KEY, creator TEXT, symbol TEXT, name TEXT, c_ts INTEGER, c_slot INTEGER,
    score REAL, level TEXT, reasons TEXT, features TEXT, scored_at INTEGER, lag_s REAL);
CREATE TABLE IF NOT EXISTS stage2 (
    mint TEXT PRIMARY KEY, org_buyers_5m INTEGER, direct_share REAL, top_prog_share REAL,
    n_progs INTEGER, flag TEXT, at INTEGER);
CREATE TABLE IF NOT EXISTS outcomes (
    mint TEXT PRIMARY KEY, graduated INTEGER, t_grad INTEGER, last_ts INTEGER, n_tr INTEGER,
    n_wal INTEGER, peak_mc REAL, rest_n INTEGER, rest_spent REAL, rest_net REAL,
    l5_spent REAL, l5_net REAL, dev_net REAL, closed TEXT, at INTEGER);
"""

REASON_TEXT = {
    "dev_supply": lambda v: f"дев выкупил {v:.0%} сапплая в первом блоке",
    "nondev_s0": lambda v: f"другие кошельки выкупили {v:.0%} в первом блоке",
    "s2_supply": lambda v: f"{v:.0%} сапплая выкуплено за первые 3 слота",
    "s0_buyers": lambda v: f"{v:.0f} покупателей в первом блоке, кроме дева",
    "multi_tx": lambda v: f"{v:.0f} транзакций с несколькими кошельками на старте",
    "max_wal_per_tx": lambda v: f"до {v:.0f} кошельков в одной транзакции",
    "dev_prev_30d": lambda v: f"у дева {v:.0f} запусков за 30 дней",
    "dev_prev_grad": lambda v: f"у дева {v:.0f} выходов на AMM за 30 дней",
    "dev_grad_rate": lambda v: f"доля выходов на AMM у дева {v:.1%}",
    "comp_before": lambda v: f"{v:.0f} запусков с этим тикером за 30 мин",
    "mayhem": lambda v: "режим mayhem",
    "n_wal_10s": lambda v: f"{v:.0f} кошельков за 10 с",
    "net_supply_10s": lambda v: f"за 10 с чистыми выкуплено {v:.0%}",
}


REASON_TEXT_EN = {
    "dev_supply": lambda v: f"dev bought {v:.0%} of supply in the first block",
    "nondev_s0": lambda v: f"other wallets bought {v:.0%} of supply in the first block",
    "s2_supply": lambda v: f"{v:.0%} of supply bought in the first 3 slots",
    "s0_buyers": lambda v: f"{v:.0f} non-dev buyers in the first block",
    "multi_tx": lambda v: f"{v:.0f} multi-wallet transactions at launch",
    "max_wal_per_tx": lambda v: f"up to {v:.0f} wallets in one transaction",
    "dev_prev_30d": lambda v: f"dev launched {v:.0f} tokens in 30 days",
    "dev_prev_grad": lambda v: f"dev had {v:.0f} graduations in 30 days",
    "dev_grad_rate": lambda v: f"dev graduation rate {v:.1%}",
    "comp_before": lambda v: f"{v:.0f} launches with this ticker in 30 min",
    "mayhem": lambda v: "mayhem mode",
    "n_wal_10s": lambda v: f"{v:.0f} wallets in 10 s",
    "net_supply_10s": lambda v: f"{v:.0%} net supply bought in 10 s",
}


def _hide(text):
    """Ключи в URL не должны попадать в лог."""
    for k in ("api-key=", "api_key="):
        text = text.split(k)[0]
    return text


def solami_key():
    return os.environ.get("SOLAMI_API_KEY") or None


def ws_url():
    u = os.environ.get("WS_URL")
    if u:
        return u
    if solami_key():
        return mirage.WS.format(key=solami_key())
    key = os.environ.get("HELIUS_API_KEY")
    if not key:
        sys.exit("[FATAL] нет ни WS_URL, ни SOLAMI_API_KEY, ни HELIUS_API_KEY в .env")
    return f"wss://mainnet.helius-rpc.com/?api-key={key}"


def rpc_url():
    u = os.environ.get("RPC_URL")
    if u:
        return u
    if solami_key():
        return mirage.RPC.format(key=solami_key())
    key = os.environ.get("HELIUS_API_KEY")
    return f"https://mainnet.helius-rpc.com/?api-key={key}" if key else None


def get_tx(url, sig):
    """getTransaction с парой повторов: confirmed-транзакция появляется в RPC не мгновенно."""
    import requests
    body = {"jsonrpc": "2.0", "id": 1, "method": "getTransaction",
            "params": [sig, {"encoding": "jsonParsed", "commitment": "confirmed",
                             "maxSupportedTransactionVersion": 0}]}
    for delay in (0, 0.5, 1.5):
        time.sleep(delay)
        try:
            res = requests.post(url, json=body, timeout=10).json().get("result")
        except Exception:
            res = None
        if res:
            return res
    return None


class Telegram:
    """Отправка в фоновом потоке: сеть не должна тормозить приём WebSocket."""

    def __init__(self):
        self.token = os.environ.get("TG_BOT_TOKEN")
        self.chat = os.environ.get("TG_CHAT_ID")
        self.on = bool(self.token and self.chat)
        self.q = queue.Queue(maxsize=200)
        if self.on:
            threading.Thread(target=self._loop, daemon=True).start()

    def send(self, text):
        if not self.on:
            return
        try:
            self.q.put_nowait(text)
        except queue.Full:
            print("    [TG] очередь переполнена, алерт пропущен")

    def _loop(self):
        import requests
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        while True:
            text = self.q.get()
            for _ in range(3):
                try:
                    r = requests.post(url, json={"chat_id": self.chat, "text": text,
                                                 "disable_web_page_preview": True}, timeout=10)
                    if r.status_code == 429:           # лимит Telegram: ждём, сколько сказали
                        time.sleep(r.json().get("parameters", {}).get("retry_after", 5))
                        continue
                    break
                except Exception as e:
                    print(f"    [TG] не отправилось: {str(e).split('/bot')[0]}")
                    time.sleep(2)


class Scorer:
    def __init__(self, replay=False, live_db=LIVE_DB, dev_db=DEV_DB):
        self.replay = replay
        self.model = Model(MODEL_PATH)
        self.devdb = DevDB(dev_db)
        if not self.devdb.conn.execute("select 1 from launches limit 1").fetchone():
            print("[!] история девов пуста — запусти: python -m live.devdb --seed")
        self.db = sqlite3.connect(live_db, timeout=30)
        self.db.execute("pragma journal_mode=wal")
        self.db.executescript(SCHEMA)
        self.tickers = TickerWindow()
        self.wal_launches = {}       # кошелёк -> в скольких запусках торговал (для отсева ботов)
        self.launches = {}
        self.comp = {}
        self.pending = {}            # mint -> [(t, ev, slot, sig, outer)]
        self.refetch = deque()       # (sig, slot) транзакций с обрезанными логами
        self.can_refetch = not replay and bool(rpc_url())
        self.tg = Telegram()
        self.clock = 0.0             # в режиме replay — время последнего события
        self.started = time.time()
        self.source = "replay" if replay else "?"
        self.st = dict(msgs=0, failed_tx=0, creates=0, creates_other_quote=0, trades=0,
                       trades_unknown=0, completes=0, scored=0, red=0, yellow=0, stage2=0, closed=0)

    def now(self):
        return self.clock if self.replay else time.time()

    # ---------- входящие ----------
    def on_frame(self, frame):
        """Кадр Solami Mirage (bytes SubscribeUpdate): транзакция целиком, добор не нужен."""
        u = mirage.parse(frame)
        if u[0] != "tx":
            return
        _, slot, sig, failed, events = u
        self.st["msgs"] += 1
        if failed:
            self.st["failed_tx"] += 1
            return
        self.dispatch(events, slot, sig)

    def on_message(self, raw):
        if isinstance(raw, (bytes, bytearray)):
            self.on_frame(raw)
            return
        if raw.startswith('{"m":'):              # записанный кадр Mirage (--record в режиме mirage)
            import base64
            self.on_frame(base64.b64decode(json.loads(raw)["m"]))
            return
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            self.st["bad_json"] = self.st.get("bad_json", 0) + 1      # обрезанная строка записи и т.п.
            return
        if "result" in msg and "id" in msg:
            print(f"[OK] подписка logsSubscribe #{msg['result']}")
            return
        if msg.get("method") != "logsNotification":
            if "error" in msg:
                print(f"[WS ERR] {msg['error']}")
            return
        self.st["msgs"] += 1
        res = msg["params"]["result"]
        slot = res["context"]["slot"]
        val = res["value"]
        if val.get("err") is not None:
            self.st["failed_tx"] += 1
            return
        sig = val["signature"]
        logs = val.get("logs") or []
        if "Log truncated" in logs and self.can_refetch:     # строка бывает и не последней
            # хвост лога обрезан: события в нём неполные. Берём всю транзакцию через RPC
            self.st["truncated"] = self.st.get("truncated", 0) + 1
            self.refetch.append((sig, slot))
            return
        self.dispatch(events_from_logs(logs), slot, sig)

    def dispatch(self, events, slot, sig):
        for ev, outer in events:
            if self.replay:
                self.clock = max(self.clock, float(ev["timestamp"]))
            k = ev["kind"]
            if k == "create":
                self.on_create(ev, slot)
            elif k == "trade":
                self.on_trade(ev, slot, sig, outer)
            elif k == "complete":
                self.on_complete(ev)

    def on_create(self, ev, slot):
        mint = ev["mint"]
        if mint in self.launches:
            return
        # история дева и соседи по тикеру в census.sql считаются по ВСЕМ котировкам (cr_all) —
        # поэтому пишем их до фильтра. История считается строго раньше ts, добавлять можно сразу
        self.devdb.add_launch(ev["creator"], mint, ev["timestamp"])
        comp = self.tickers.add_and_count(ev["symbol"], ev["creator"], ev["timestamp"])
        if ev.get("quote_mint", SOL_QUOTE) != SOL_QUOTE:
            self.st["creates_other_quote"] += 1       # модель училась только на котировке в SOL
            return
        self.st["creates"] += 1
        L = Launch(ev, slot, self.now())
        self.launches[mint] = L
        self.comp[mint] = comp
        for _, tev, tslot, tsig, touter in self.pending.pop(mint, ()):
            self._add(L, tev, tslot, tsig, touter)

    def on_trade(self, ev, slot, sig, outer):
        self.st["trades"] += 1
        L = self.launches.get(ev["mint"])
        if L is not None:
            self._add(L, ev, slot, sig, outer)
            return
        self.st["trades_unknown"] += 1
        lst = self.pending.get(ev["mint"])
        if lst is not None or len(self.pending) < 50_000:
            self.pending.setdefault(ev["mint"], []).append((self.now(), ev, slot, sig, outer))

    def _add(self, L, ev, slot, sig, outer):
        if L.add_trade(ev, slot, sig, outer):
            self.wal_launches[ev["user"]] = self.wal_launches.get(ev["user"], 0) + 1

    def is_bot(self, u):
        """Как bots в census.sql: торгует в >= 1% запусков (агент Mayhem, объёмные боты)."""
        return self.wal_launches.get(u, 0) >= max(30, 0.01 * self.st["creates"])

    def on_complete(self, ev):
        self.st["completes"] += 1
        self.devdb.mark_graduated(ev["mint"], ev["timestamp"])
        L = self.launches.get(ev["mint"])
        if L is not None:
            L.grad_ts = ev["timestamp"]

    def mark_gap(self):
        """Обрыв WebSocket: у запусков в памяти потеряны сделки — не алертим по ним."""
        now = self.now()
        for L in self.launches.values():
            if now - L.seen_at < STAGE2_AFTER:   # окна 10 с и 5 мин задеты обрывом
                L.gap = True
        self.pending.clear()

    # ---------- таймеры ----------
    def tick(self):
        now = self.now()
        for mint, L in list(self.launches.items()):
            age = now - L.seen_at
            if not L.scored and age >= DECIDE_AFTER:
                self.score(L)
            if not L.stage2_done and age >= STAGE2_AFTER:
                self.stage2(L)
            silent = now - L.last_ts
            if L.scored and L.stage2_done and (silent > EVICT_SILENT or now - L.ts > EVICT_AGE):
                self.close(L, "silent" if silent > EVICT_SILENT else "age")
        for mint in [m for m, v in self.pending.items() if now - v[0][0] > PENDING_TTL]:
            del self.pending[mint]
        self.db.commit()
        self.devdb.commit()

    def score(self, L):
        f = L.features_10s(self.devdb, self.comp.get(L.mint, 0))
        p = self.model.predict(f)
        lvl = self.model.level(p)
        reasons = [(k, v, r) for k, v, r in self.model.reasons(f)]
        txt = "; ".join(f"{REASON_TEXT[k](v)} (ловушек в такой группе {r:.0%})" for k, v, r in reasons)
        L.scored, L.score = True, p
        if L.gap:
            lvl = "gap"                # окно 10 с пересекло обрыв: признаки неполные
        lag = self.now() - L.seen_at
        self.db.execute("insert or replace into scores values (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (L.mint, L.creator, L.symbol, L.name, L.ts, L.slot, p, lvl, txt,
                         json.dumps(f), int(self.now()), lag))
        self.st["scored"] += 1
        if lvl == "yellow":
            self.st["yellow"] += 1
        if lvl == "red":
            self.st["red"] += 1
            active = (f["n_wal_10s"] or 0) >= MIN_WALLETS_ALERT
            line = (f"[{time.strftime('%H:%M:%S')}] 🔴 {L.symbol[:12]:12s} {p:.2f} "
                    f"| {L.mint} | {txt or 'комбинация признаков'}")
            print(line)
            if active:
                self.tg.send(self.alert_text(L, p, reasons))

    def alert_text(self, L, p, reasons):
        base = self.model.meta["base_rate"]
        if ALERT_LANG == "ru":
            txt = "\n".join(f"├ {REASON_TEXT[k](v)} (ловушек в такой группе {r:.0%})" for k, v, r in reasons)
            return (f"🔴 ${L.symbol} — риск {p:.0%} (база {base:.0%})\n{txt or '├ комбинация признаков'}\n"
                    f"└ https://pump.fun/coin/{L.mint}")
        txt = "\n".join(f"├ {REASON_TEXT_EN[k](v)} — {r:.0%} of such launches were traps" for k, v, r in reasons)
        return (f"🔴 ${L.symbol} — trap risk {p:.0%} (market base {base:.0%})\n"
                f"{txt or '├ combination of signals'}\n"
                f"├ scored {self.now() - L.seen_at:.0f} s after creation · source: {self.source}\n"
                f"└ https://pump.fun/coin/{L.mint}")

    def stage2(self, L):
        s = L.features_5m()
        flag = None
        if s["org_buyers_5m"] >= 5 and (s["direct_share"] or 0) >= 0.99 and not L.gap:
            flag = "all_direct"        # все ранние мимо терминалов: сайт pump.fun или свои скрипты
        L.stage2_done = True
        L.trades = []                  # первые 5 минут больше не нужны
        self.db.execute("insert or replace into stage2 values (?,?,?,?,?,?,?)",
                        (L.mint, s["org_buyers_5m"], s["direct_share"], s["top_prog_share"],
                         s["n_progs"], flag, int(self.now())))
        self.st["stage2"] += 1
        if flag:
            print(f"[{time.strftime('%H:%M:%S')}] 🟠 {L.symbol[:12]:12s} все {s['org_buyers_5m']} ранних "
                  f"покупателей зашли прямым вызовом pump | {L.mint}")

    def close(self, L, why):
        o = L.outcome(self.is_bot)
        self.db.execute("insert or replace into outcomes values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (L.mint, int(o["graduated"]), o["t_grad"], o["last_ts"], o["n_tr"], o["n_wal"],
                         o["peak_mc"], o["rest_n"], o["rest_spent"], o["rest_net"], o["l5_spent"],
                         o["l5_net"], o["dev_net"], "gap" if L.gap else why, int(self.now())))
        del self.launches[L.mint]
        self.comp.pop(L.mint, None)
        self.st["closed"] += 1

    def flush_all(self, close=False):
        for L in list(self.launches.values()):
            if not L.scored and self.now() - L.seen_at >= DECIDE_AFTER:
                self.score(L)
            if close and L.scored:
                self.close(L, "shutdown")      # иначе в разметку попадают только умершие за сессию
        self.db.commit()
        self.devdb.commit()

    def report(self):
        s = self.st
        up = (time.time() - self.started) / 60
        print(f"[{time.strftime('%H:%M:%S')}] {up:5.1f} мин | сообщений {s['msgs']:,} | запусков {s['creates']:,} "
              f"| сделок {s['trades']:,} | выходов на AMM {s['completes']} | оценено {s['scored']:,} "
              f"(🔴 {s['red']}, 🟡 {s['yellow']}) | в памяти {len(self.launches):,}")
        if s["msgs"] > 200 and s["creates"] == 0 and s["trades"] == 0:
            print("  [!] сообщения идут, но событий pump не разобрано. Скорее всего, событий нет в логах "
                  "(только внутренние инструкции) — пришли мне пару строк из --record")


async def refetch_worker(sc):
    url = rpc_url()
    while True:
        if not sc.refetch:
            await asyncio.sleep(0.2)
            continue
        sig, slot = sc.refetch.popleft()
        tx = await asyncio.to_thread(get_tx, url, sig)
        if tx is None:
            sc.st["refetch_failed"] = sc.st.get("refetch_failed", 0) + 1
            continue
        try:
            sc.dispatch(events_from_tx(tx), slot, sig)
        except Exception as e:
            print(f"[ERR] refetch {sig[:12]}: {type(e).__name__}: {e}")


async def run_live(sc, record, record_max):
    import websockets
    url = ws_url()
    worker = asyncio.create_task(refetch_worker(sc)) if sc.can_refetch else None  # noqa: F841 держим ссылку
    sub = {"jsonrpc": "2.0", "id": 1, "method": "logsSubscribe",
           "params": [{"mentions": [PUMP_PROGRAM]}, {"commitment": "confirmed"}]}
    rec = open(record, "a", encoding="utf-8") if record else None
    written = 0
    last_tick = last_rep = 0.0
    while True:
        try:
            async with websockets.connect(url, ping_interval=20, ping_timeout=20, max_size=16 * 2**20) as ws:
                await ws.send(json.dumps(sub))
                print(f"[..] подключился к {url.split('?')[0]} (logsSubscribe)")
                while True:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        if rec:
                            rec.write(raw if isinstance(raw, str) else raw.decode())
                            rec.write("\n")
                            written += 1
                            if written >= record_max:      # поток pump — десятки ГБ в сутки
                                rec.close(); rec = None
                                print(f"[..] записал {written:,} сообщений в {record}, запись остановлена")
                        try:
                            sc.on_message(raw)
                        except Exception as e:           # одно битое сообщение не должно рвать поток
                            sc.st["errors"] = sc.st.get("errors", 0) + 1
                            if sc.st["errors"] <= 20:
                                print(f"[ERR] сообщение пропущено: {type(e).__name__}: {e}")
                    except asyncio.TimeoutError:
                        pass
                    t = time.time()
                    if t - last_tick >= 1:
                        try:
                            sc.tick()
                        except Exception as e:
                            print(f"[ERR] tick: {type(e).__name__}: {e}")
                        last_tick = t
                    if t - last_rep >= 60:
                        sc.report(); last_rep = t
        except (KeyboardInterrupt, asyncio.CancelledError):
            raise
        except Exception as e:
            print(f"[WS] {type(e).__name__}: {_hide(str(e))} — переподключаюсь через 5 с")
            sc.mark_gap()
            await asyncio.sleep(5)


async def run_mirage(sc, sid, record, record_max):
    """Solami Mirage: бинарные кадры SubscribeUpdate по сохранённой подписке на программу pump."""
    import base64
    import websockets
    url = mirage.stream_url(solami_key(), sid)
    rec = open(record, "a", encoding="utf-8") if record else None
    written = 0
    last_tick = last_rep = 0.0
    backoff = 2
    while True:
        try:
            async with websockets.connect(url, ping_interval=20, ping_timeout=20, max_size=64 * 2**20) as ws:
                print(f"[..] подключился к Solami Mirage, подписка {sid[:8]}… (транзакции целиком, без добора)")
                backoff = 2
                while True:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        if isinstance(raw, str):         # служебное текстовое сообщение
                            print(f"[MIRAGE] {_hide(raw)[:300]}")
                        else:
                            if rec:
                                rec.write(json.dumps({"m": base64.b64encode(raw).decode()}))
                                rec.write("\n")
                                written += 1
                                if written >= record_max:
                                    rec.close(); rec = None
                                    print(f"[..] записал {written:,} кадров в {record}, запись остановлена")
                            try:
                                sc.on_frame(raw)
                            except Exception as e:
                                sc.st["errors"] = sc.st.get("errors", 0) + 1
                                if sc.st["errors"] <= 20:
                                    print(f"[ERR] кадр пропущен: {type(e).__name__}: {e}")
                    except asyncio.TimeoutError:
                        pass
                    t = time.time()
                    if t - last_tick >= 1:
                        try:
                            sc.tick()
                        except Exception as e:
                            print(f"[ERR] tick: {type(e).__name__}: {e}")
                        last_tick = t
                    if t - last_rep >= 60:
                        sc.report(); last_rep = t
        except (KeyboardInterrupt, asyncio.CancelledError):
            raise
        except Exception as e:
            code = getattr(e, "code", None) or getattr(getattr(e, "rcvd", None), "code", None)
            hint = {4029: "лимит одновременных стримов", 4002: "кончились трафик и баланс",
                    1001: "Solami перезапускает узел"}.get(code, "")
            print(f"[MIRAGE] {type(e).__name__}: {_hide(str(e))[:200]} {hint} — переподключаюсь через {backoff} с")
            sc.mark_gap()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


def run_replay(sc, path):
    last = 0.0
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            sc.on_message(line)
            if sc.clock - last >= 1:
                sc.tick(); last = sc.clock
    sc.clock += STAGE2_AFTER + 1
    sc.tick()
    sc.flush_all()
    sc.report()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--record", help="дописывать сырые сообщения WebSocket в файл")
    ap.add_argument("--record-max", type=int, default=20000, help="сколько сообщений записать")
    ap.add_argument("--replay", help="прогнать записанный файл без сети")
    ap.add_argument("--source", choices=["auto", "mirage", "ws"], default=os.environ.get("SOURCE", "auto"),
                    help="mirage — Solami Mirage; ws — logsSubscribe; auto — mirage при SOLAMI_API_KEY")
    a = ap.parse_args()
    src_kind = a.source
    if src_kind == "auto":
        src_kind = "mirage" if solami_key() else "ws"
    if src_kind == "mirage" and not solami_key():
        sys.exit("[FATAL] --source mirage: нет SOLAMI_API_KEY в .env")
    sid = None
    if src_kind == "mirage" and not a.replay:
        try:
            sid = mirage.ensure_subscription(solami_key())
        except Exception as e:
            if a.source != "auto":
                sys.exit(f"[FATAL] {_hide(str(e))}")
            print(f"[!] {_hide(str(e))}\n[!] Mirage недоступен — работаю через logsSubscribe")
            src_kind = "ws"
    if a.replay:
        # replay не трогает боевые базы: история девов — снимком, оценки — в replay/live.db
        rdir = Path(os.environ.get("REPLAY_DIR", ROOT / "replay"))
        rdir.mkdir(exist_ok=True)
        for suf in ("", "-wal", "-shm"):
            (rdir / f"live.db{suf}").unlink(missing_ok=True)
        src, dst = sqlite3.connect(DEV_DB), sqlite3.connect(rdir / "devdb.sqlite")
        src.backup(dst); dst.close(); src.close()
        sc = Scorer(replay=True, live_db=rdir / "live.db", dev_db=rdir / "devdb.sqlite")
    else:
        sc = Scorer()
        if src_kind == "mirage":
            sc.source = "Solami Mirage"
            sc.can_refetch = False         # транзакции приходят целиком
        else:
            sc.source = "Solami WS" if ("solami" in ws_url()) else "WebSocket"
    m = sc.model.meta
    print("=" * 72)
    print("  ClusterPulse — живой скорер")
    print(f"  модель: {m['trained_on']}, проверка на отложенной неделе AUC {m['holdout']['auc']:.3f}")
    print(f"  🔴 от {sc.model.red:.2f} (точность на проверке {m['precision_at']['p98']:.0%}), "
          f"🟡 от {sc.model.yellow:.2f} ({m['precision_at']['p90']:.0%}); база {m['holdout']['base']:.0%}")
    print(f"  решение через {DECIDE_AFTER:.0f} с после создания | Telegram: {'вкл' if sc.tg.on else 'выкл'} "
          f"({ALERT_LANG}) | источник: {sc.source}")
    print("=" * 72)
    try:
        if a.replay:
            run_replay(sc, a.replay)
        elif src_kind == "mirage":
            asyncio.run(run_mirage(sc, sid, a.record, a.record_max))
        else:
            asyncio.run(run_live(sc, a.record, a.record_max))
    except KeyboardInterrupt:
        sc.flush_all(close=not a.replay)
        print("\n[STOP] остановлено, всё записано в live.db")


if __name__ == "__main__":
    main()
