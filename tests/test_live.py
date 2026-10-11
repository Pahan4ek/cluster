"""Офлайн-проверка: синтетические логи по IDL -> декодер -> признаки -> скор.
    python tests/test_live.py
"""
import base64, json, os, sqlite3, struct, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from live.pump_events import (b58encode, b58decode, DISC_TRADE, DISC_CREATE, DISC_COMPLETE, EVENT_IX_TAG,
                              PUMP_PROGRAM, SOL_QUOTE, decode_ix_data, events_from_logs)

def pk(i): return bytes([i]) * 32
def s_(x): b = x.encode(); return struct.pack("<I", len(b)) + b
u64 = lambda v: struct.pack("<Q", v); i64 = lambda v: struct.pack("<q", v); bl = lambda v: bytes([1 if v else 0])
Q = lambda addr: b58decode(addr).rjust(32, b"\0")

def trade(mint, user, is_buy, sol, tok, ts, creator, vs=30_000_000_000, vt=1_073_000_000_000_000):
    b = DISC_TRADE + mint + u64(sol) + u64(tok) + bl(is_buy) + user + i64(ts) + u64(vs) + u64(vt)
    b += u64(0) + u64(0) + pk(99) + u64(95) + u64(1000) + creator + u64(30) + u64(500) + bl(False)
    b += u64(0) + u64(0) + u64(0) + i64(0) + s_("buy" if is_buy else "sell") + bl(False)
    b += u64(0) + u64(0) + u64(0) + u64(0) + struct.pack("<I", 0) + Q(SOL_QUOTE) + u64(sol) + u64(0) + u64(0) + u64(0) + u64(0)
    return b

def create(mint, creator, sym, ts, mayhem=False, quote=SOL_QUOTE):
    b = DISC_CREATE + s_("Name " + sym) + s_(sym) + s_("ipfs://x") + mint + pk(50) + creator + creator + i64(ts)
    b += u64(1_073_000_000_000_000) + u64(30_000_000_000) + u64(793_100_000_000_000) + u64(10**15) + pk(51)
    b += bl(mayhem) + bl(False) + Q(quote) + u64(0) + u64(0) + bl(False)
    return b

def complete(mint, ts): return DISC_COMPLETE + pk(60) + mint + pk(50) + i64(ts) + Q(SOL_QUOTE)

ROUTER = b58encode(pk(77))
def logs(events, outer=PUMP_PROGRAM):
    L = ["Program ComputeBudget111111111111111111111111111111 invoke [1]", "Program ComputeBudget111111111111111111111111111111 success"]
    depth = 1
    if outer != PUMP_PROGRAM:
        L.append(f"Program {outer} invoke [1]"); L.append(f"Program data: " + base64.b64encode(DISC_TRADE + b"\0" * 40).decode())  # чужое событие роутера
        depth = 2
    L.append(f"Program {PUMP_PROGRAM} invoke [{depth}]"); L.append("Program log: Instruction: Buy")
    for e in events:
        L.append("Program data: " + base64.b64encode(e).decode())
    L.append(f"Program {PUMP_PROGRAM} invoke [{depth+1}]"); L.append(f"Program {PUMP_PROGRAM} success")
    L.append(f"Program {PUMP_PROGRAM} consumed 1000 of 200000 compute units"); L.append(f"Program {PUMP_PROGRAM} success")
    if outer != PUMP_PROGRAM: L.append(f"Program {outer} success")
    return L

def note(slot, sig, lg, err=None):
    return json.dumps({"jsonrpc": "2.0", "method": "logsNotification", "params": {"subscription": 1,
        "result": {"context": {"slot": slot}, "value": {"signature": sig, "err": err, "logs": lg}}}})

# --- 1. декодер
M, DEV = pk(1), pk(2); A, B, C, D, E = pk(3), pk(4), pk(5), pk(6), pk(7)
T0, S0 = 1_790_100_000, 450_000_000
evs = events_from_logs(logs([create(M, DEV, "TEST", T0), trade(M, DEV, True, 1_000_000_000, 50_000_000_000_000, T0, DEV)]))
assert [e["kind"] for e, _ in evs] == ["create", "trade"], evs
c, t = evs[0][0], evs[1][0]
assert c["mint"] == b58encode(M) and c["creator"] == b58encode(DEV) and c["symbol"] == "TEST" and c["quote_mint"] == SOL_QUOTE
assert t["sol_amount"] == 1_000_000_000 and t["token_amount"] == 50_000_000_000_000 and t["is_buy"] and t["ix_name"] == "buy"
assert evs[1][1] == PUMP_PROGRAM
ev_r = events_from_logs(logs([trade(M, A, True, 1, 1, T0, DEV)], outer=ROUTER))
assert len(ev_r) == 1 and ev_r[0][1] == ROUTER, ev_r               # чужое "Program data" роутера не принято за pump
assert decode_ix_data(b58encode(EVENT_IX_TAG + trade(M, A, True, 5, 6, T0, DEV)))["sol_amount"] == 5
assert b58decode(b58encode(pk(0) + pk(9))) == pk(0) + pk(9)
print("декодер: ок")

# --- 2. сценарий: дев + бандл в слоте 0, роутер в слоте 3, поздняя розница, выход на AMM
m = lambda i: b58encode(i)
msgs = [
    note(S0, "sig_create", logs([create(M, DEV, "TEST", T0), trade(M, DEV, True, 1_000_000_000, 50_000_000_000_000, T0, DEV)])),
    note(S0, "sig_bundle", logs([trade(M, A, True, 2_000_000_000, 60_000_000_000_000, T0, DEV),
                                 trade(M, B, True, 2_000_000_000, 40_000_000_000_000, T0, DEV),
                                 trade(M, C, True, 1_000_000_000, 20_000_000_000_000, T0, DEV)])),
    note(S0 + 1, "sig_fail", logs([trade(M, E, True, 9_000_000_000, 90_000_000_000_000, T0, DEV)]), err={"InstructionError": [0, 1]}),
    note(S0 + 3, "sig_router", logs([trade(M, D, True, 500_000_000, 10_000_000_000_000, T0 + 2, DEV)], outer=ROUTER)),
    note(S0 + 20, "sig_sellA", logs([trade(M, A, False, 2_500_000_000, 60_000_000_000_000, T0 + 8, DEV)])),
    note(S0 + 40, "sig_late", logs([trade(M, E, True, 3_000_000_000, 30_000_000_000_000, T0 + 15, DEV)])),
    note(S0 + 50, "sig_other_quote", logs([create(pk(8), DEV, "USDX", T0 + 16, quote=b58encode(pk(88)))])),
    note(S0 + 900, "sig_devsell", logs([trade(M, DEV, False, 4_000_000_000, 50_000_000_000_000, T0 + 400, DEV)])),
    note(S0 + 950, "sig_complete", logs([complete(M, T0 + 420)])),
]
tmp = Path(tempfile.mkdtemp())
(tmp / "raw.jsonl").write_text("\n".join(msgs), encoding="utf-8")
import live_scorer, live.devdb as dvd
live_scorer.LIVE_DB = tmp / "live.db"
dvd.DB_PATH = tmp / "devdb.sqlite"
sc = live_scorer.Scorer.__new__(live_scorer.Scorer)
# собираем Scorer с временными базами
from live.devdb import DevDB
def init(self):
    self.replay = True
    self.model = live_scorer.Model(ROOT / "model" / "live_model.json")
    self.devdb = DevDB(tmp / "devdb.sqlite")
    self.devdb.add_launch(m(DEV), "old1", T0 - 86400); self.devdb.add_launch(m(DEV), "old2", T0 - 3600)
    self.devdb.mark_graduated("old1", T0 - 80000)
    self.devdb.add_launch(m(DEV), "ancient", T0 - 40 * 86400)
    self.db = sqlite3.connect(tmp / "live.db"); self.db.executescript(live_scorer.SCHEMA)
    self.wal_launches = {}
    self.tickers = live_scorer.TickerWindow(); self.tickers.add_and_count("test", m(pk(90)), T0 - 60)
    self.launches, self.comp, self.pending, self.skip = {}, {}, {}, set()
    from collections import deque
    self.refetch, self.can_refetch = deque(), False
    self.tg = live_scorer.Telegram(); self.tg.on = False
    self.clock, self.started = 0.0, 0
    self.st = dict(msgs=0, failed_tx=0, creates=0, creates_other_quote=0, trades=0, trades_unknown=0,
                   completes=0, scored=0, red=0, yellow=0, stage2=0, closed=0)
init(sc)
live_scorer.run_replay(sc, tmp / "raw.jsonl")
row = sc.db.execute("select features, score, level from scores where mint=?", (m(M),)).fetchone()
f = json.loads(row[0])
exp = {"dev_supply": 0.05, "nondev_s0": 0.12, "s2_supply": 0.17, "s0_buyers": 3, "multi_tx": 1, "max_wal_per_tx": 3,
       "dev_prev_30d": 2, "dev_prev_grad": 1, "dev_grad_rate": 0.5, "comp_before": 1, "mayhem": 0,
       "n_wal_10s": 5, "net_supply_10s": 0.12}
for k, v in exp.items():
    assert abs((f[k] or 0) - v) < 1e-9, (k, f[k], v)
print("признаки 10 с: ок", {k: round(v, 3) if isinstance(v, float) else v for k, v in f.items()})
print(f"скор {row[1]:.3f} -> {row[2]}")
s2 = sc.db.execute("select org_buyers_5m, direct_share, top_prog_share, n_progs from stage2 where mint=?", (m(M),)).fetchone()
assert s2 == (2, 0.5, 0.5, 2), s2          # D через роутер, E напрямую (A,B,C в слоте 0 не считаются)
print("второй этап: ок", s2)
assert sc.st["failed_tx"] == 1 and sc.st["creates_other_quote"] == 1 and sc.st["completes"] == 1
L = sc.launches[m(M)]
o = L.outcome()
assert o["graduated"] and o["t_grad"] == 420
assert abs(o["rest_spent"] - 3.0) < 1e-9 and abs(o["rest_net"] + 3.0) < 1e-9, o   # E: первая сделка в слоте 40 (>5)
assert abs(o["dev_net"] - 3.0) < 1e-9, o
print("исходы: ок", {k: o[k] for k in ("rest_n", "rest_spent", "rest_net", "dev_net", "graduated")})

# --- 4. обрезанный лог: уходит в очередь, события берутся из getTransaction
from live.pump_events import events_from_tx
M2 = pk(11)
tx = {"meta": {"err": None, "innerInstructions": [{"index": 1, "instructions": [
        {"programId": PUMP_PROGRAM, "data": b58encode(bytes([102]) * 8)},                  # сама инструкция buy
        {"programId": PUMP_PROGRAM, "data": b58encode(EVENT_IX_TAG + create(M2, DEV, "TRNC", T0 + 500))},
        {"programId": PUMP_PROGRAM, "data": b58encode(EVENT_IX_TAG + trade(M2, A, True, 10**9, 3 * 10**13, T0 + 500, DEV))}]}]},
      "transaction": {"message": {"instructions": [{"programId": "ComputeBudget111111111111111111111111111111"},
                                                   {"programId": ROUTER}]}}}
ev = events_from_tx(tx)
assert [e["kind"] for e, _ in ev] == ["create", "trade"] and all(o == ROUTER for _, o in ev), ev
sc.can_refetch = True
sc.on_message(note(S0 + 2000, "sig_trunc", logs([create(M2, DEV, "TRNC", T0 + 500)])[:4] + ["Log truncated"]))
assert list(sc.refetch) == [("sig_trunc", S0 + 2000)] and m(M2) not in sc.launches
sc.dispatch(ev, S0 + 2000, "sig_trunc")
assert sc.launches[m(M2)].trades[0][3] == m(A)
print("обрезанные логи: ок")

# --- 5. обрыв WebSocket: молодой запуск помечается, алерта нет
sc.mark_gap()
sc.clock += 20; sc.tick()
assert sc.db.execute("select level from scores where mint=?", (m(M2),)).fetchone()[0] == "gap"
print("обрыв: ок")
print("ВСЁ ОК")
