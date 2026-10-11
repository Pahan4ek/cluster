"""Офлайн-прогон tools/funding_check.py на выдуманной истории минта (jsonParsed, события в emit_cpi).
    python tests/test_funding_check.py
"""
import io, sys, contextlib
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
src = (ROOT / "tests" / "test_live.py").read_text(encoding="utf-8").split("# --- 1. декодер")[0]
ns = {"__file__": str(ROOT / "tests" / "test_live.py")}
exec(compile(src, "test_live_head", "exec"), ns)
pk, trade, create = ns["pk"], ns["trade"], ns["create"]
from live.pump_events import EVENT_IX_TAG, PUMP_PROGRAM, b58encode
import funding_check

M, DEV = pk(1), pk(2)
mint, dev = b58encode(M), b58encode(DEV)
T0 = 1_790_100_000
bundle = [pk(10 + i) for i in range(5)]           # пополнены девом
real = [pk(30 + i) for i in range(2)]              # пополнены с разных адресов
W = {b58encode(x): ("dev" if x in bundle else "own") for x in bundle + real}


def ptx(raw, slot):
    return {"slot": slot, "meta": {"err": None, "innerInstructions": [{"index": 0, "instructions": [
        {"programId": PUMP_PROGRAM, "data": b58encode(EVENT_IX_TAG + raw)}]}]},
        "transaction": {"message": {"instructions": [{"programId": PUMP_PROGRAM}]}}}


H = [("c", ptx(create(M, DEV, "SCAM", T0), 1))]
for i, b in enumerate(bundle):
    H.append((f"b{i}", ptx(trade(M, b, True, 200_000_000, 10**12, T0 + 5 + i, DEV), 2 + i)))
for i, b in enumerate(real):
    H.append((f"r{i}", ptx(trade(M, b, True, 3_000_000_000, 10**12, T0 + 60 + i, DEV), 20 + i)))
H.append(("dsell", ptx(trade(M, DEV, False, 5_000_000_000, 10**13, T0 + 600, DEV), 50)))
for i, b in enumerate(bundle):
    H.append((f"bs{i}", ptx(trade(M, b, False, 250_000_000, 10**12, T0 + 601, DEV), 51)))
for i, b in enumerate(real):
    H.append((f"rs{i}", ptx(trade(M, b, False, 150_000_000, 10**12, T0 + 900, DEV), 60)))
TX = dict(H)


def transfer(src, dst, lam):
    return {"program": "system", "parsed": {"type": "transfer", "info": {"source": src, "destination": dst, "lamports": lam}}}


class FakeRPC:
    calls = errors = 0

    def sigs(self, addr, before=None, limit=1000):
        if addr == mint:
            return [{"signature": s} for s, _ in reversed(H)][:limit]
        if addr == dev:
            return [{"signature": "fund-dev", "blockTime": T0 - 3600}]
        return [{"signature": f"fund-{addr}", "blockTime": T0 - 600}]

    def tx(self, sig):
        if sig in TX:
            return TX[sig]
        w = dev if sig == "fund-dev" else sig[5:]
        src = "FUNDERofDEV" if w == dev else dev if W.get(w) == "dev" else "own" + w[:6]
        return {"meta": {"err": None, "innerInstructions": []},
                "transaction": {"message": {"instructions": [transfer(src, w, 1_000_000_000)]}}}


out = io.StringIO()
with contextlib.redirect_stdout(out):
    funding_check.check(FakeRPC(), mint, 5, 40, {})
txt = out.getvalue()
print(txt)
assert "кластер дева: 5 кошельков, 14% SOL" in txt and "пополнен с FUND" in txt, txt
assert "остальные ранние              2" in txt and "-95%" in txt, txt
print("ВСЁ ОК")
