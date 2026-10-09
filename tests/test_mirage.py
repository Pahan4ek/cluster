"""Офлайн-проверка разбора Solami Mirage (SubscribeUpdate) против разбора логов.
    python tests/test_mirage.py
1) синтетика: create/trade напрямую и через роутер, ALT-адреса, упавшая транзакция, ping;
2) записанный поток raw_sample.jsonl: каждую logsNotification перекладываем в кадр Mirage
   (события -> emit_cpi во внутренних инструкциях) и сверяем события с events_from_logs.
"""
import base64, json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from live.pump_events import EVENT_IX_TAG, PUMP_PROGRAM, b58decode, b58encode, decode_event, events_from_logs
from live import mirage


# ---------- кодировщик protobuf (только для теста) ----------
def varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)

def fv(num, n): return varint(num << 3) + varint(n)                 # varint-поле
def fb(num, b): return varint(num << 3 | 2) + varint(len(b)) + b    # length-delimited

def frame(slot, sig, groups, failed=False, alt=False, junk=True):
    """groups: [(outer_program, [raw_event_bytes, ...])] — одна верхняя инструкция на группу."""
    payer = bytes([9]) * 32
    progs = []
    for outer, _ in groups:
        if outer not in progs:
            progs.append(outer)
    static = [payer] + [b58decode(p).rjust(32, b"\0") for p in progs if not (alt and p == PUMP_PROGRAM)]
    loaded_w = [b58decode(PUMP_PROGRAM).rjust(32, b"\0")] if alt else []
    if alt and PUMP_PROGRAM not in progs:
        pass
    keys = static + loaded_w
    kidx = {b58encode(k): i for i, k in enumerate(keys)}
    if PUMP_PROGRAM not in kidx:                  # pump вызывается роутером, но в ключах он есть всегда
        keys.append(b58decode(PUMP_PROGRAM).rjust(32, b"\0"))
        static.append(keys[-1])
        kidx[PUMP_PROGRAM] = len(keys) - 1
    msg = b"".join(fb(2, k) for k in static)
    msg += fb(4, fv(1, 0) + fb(3, b"\x02\x00"))   # ComputeBudget-подобная инструкция без событий
    top_index = {}
    for outer, _ in groups:
        if outer not in top_index:
            top_index[outer] = 1 + len(top_index)
            msg += fb(4, fv(1, kidx[outer]) + fb(2, b"\x00\x01") + fb(3, b"\x66\x06"))
    tx = fb(1, sig) + fb(2, msg)
    meta = fv(2, 5000)
    if failed:
        meta += fb(1, fb(1, b"\x01\x00\x00\x00"))
    for outer, evs in groups:
        ixs = b""
        if junk:                                  # чужая внутренняя инструкция (перевод токенов)
            ixs += fb(2, fv(1, 0) + fb(3, b"\x0c" + b"\x00" * 8))
        for raw in evs:
            ixs += fb(2, fv(1, kidx[PUMP_PROGRAM]) + fb(2, b"\x00") + fb(3, EVENT_IX_TAG + raw) + fv(4, 2))
        meta += fb(5, fv(1, top_index[outer]) + ixs)
    for k in loaded_w:
        meta += fb(12, k)
    info = fb(1, sig) + fb(3, tx) + fb(4, meta) + fv(5, 7)
    return fb(1, b"clusterpulse") + fb(4, fb(1, info) + fv(2, slot))


def raw_events_from_logs(logs):
    """Как events_from_logs, но отдаёт сырые байты события и программу верхнего уровня."""
    out, stack = [], []
    for line in logs or ():
        if line.startswith("Program data: "):
            if stack and stack[-1] == PUMP_PROGRAM:
                try:
                    raw = base64.b64decode(line[14:])
                except ValueError:
                    continue
                if decode_event(raw):
                    out.append((raw, stack[0]))
            continue
        if not line.startswith("Program "):
            continue
        p = line.split(" ")
        if len(p) >= 4 and p[2] == "invoke":
            try:
                d = int(p[3].strip("[]"))
            except ValueError:
                continue
            del stack[d - 1:]
            stack.append(p[1])
        elif len(p) >= 3 and (p[2] == "success" or p[2].startswith("failed")):
            if stack and stack[-1] == p[1]:
                stack.pop()
    return out


# ---------- 1. синтетика ----------
sys.path.insert(0, str(ROOT / "tests"))
import importlib.util
spec = importlib.util.spec_from_file_location("tl", ROOT / "tests" / "test_live.py")
# берём построители событий из test_live, не выполняя его целиком
src = (ROOT / "tests" / "test_live.py").read_text(encoding="utf-8").split("# --- 1. декодер")[0]
ns = {"__file__": str(ROOT / "tests" / "test_live.py")}
exec(compile(src, "test_live_head", "exec"), ns)
pk, trade, create, ROUTER = ns["pk"], ns["trade"], ns["create"], ns["ROUTER"]

M, DEV, A = pk(1), pk(2), pk(3)
T0 = 1_790_100_000
c_raw = create(M, DEV, "TEST", T0)
t_raw = trade(M, DEV, True, 1_000_000_000, 50_000_000_000_000, T0, DEV)
r_raw = trade(M, A, True, 2_000_000_000, 1, T0, DEV)
sig = bytes(range(64))

k, slot, s, failed, evs = mirage.parse(frame(123, sig, [(PUMP_PROGRAM, [c_raw, t_raw]), (ROUTER, [r_raw])]))
assert k == "tx" and slot == 123 and s == b58encode(sig) and not failed
assert [(e["kind"], o) for e, o in evs] == [("create", PUMP_PROGRAM), ("trade", PUMP_PROGRAM), ("trade", ROUTER)], evs
assert evs[0][0]["symbol"] == "TEST" and evs[2][0]["user"] == b58encode(A) and evs[2][0]["sol_amount"] == 2_000_000_000

k, slot, s, failed, evs = mirage.parse(frame(124, sig, [(ROUTER, [r_raw])], alt=True))   # pump из address lookup table
assert evs and evs[0][1] == ROUTER and evs[0][0]["kind"] == "trade", evs

k, slot, s, failed, evs = mirage.parse(frame(125, sig, [(PUMP_PROGRAM, [t_raw])], failed=True))
assert failed and evs == []

assert mirage.parse(fb(1, b"x") + fb(6, b"")) == ("ping",)
assert mirage.parse(fb(1, b"x") + fb(3, fv(1, 5))) == ("other",)
print("синтетика: ок")

# ---------- 2. записанный поток ----------
path = ROOT / "raw_sample.jsonl"
n_tx = n_ev = n_trunc = 0
if path.exists():
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("method") != "logsNotification":
                continue
            v = msg["params"]["result"]["value"]
            if v.get("err") is not None:
                continue
            logs = v.get("logs") or []
            if "Log truncated" in logs:
                n_trunc += 1
                continue
            want = events_from_logs(logs)
            groups = []
            for raw, outer in raw_events_from_logs(logs):
                if groups and groups[-1][0] == outer:
                    groups[-1][1].append(raw)
                else:
                    groups.append((outer, [raw]))
            slot = msg["params"]["result"]["context"]["slot"]
            sigb = b58decode(v["signature"])
            k, s_slot, s_sig, failed, got = mirage.parse(frame(slot, sigb, groups))
            assert s_slot == slot and s_sig == v["signature"], (s_slot, s_sig)
            assert got == want, (v["signature"], got, want)
            n_tx += 1
            n_ev += len(got)
            if n_tx >= 20000:
                break
    assert n_tx > 1000, n_tx
    print(f"поток: {n_tx:,} транзакций, {n_ev:,} событий совпали с разбором логов (обрезанных пропущено {n_trunc})")
else:
    print("raw_sample.jsonl нет — сверка с потоком пропущена")

# ---------- 3. скорер целиком: логи vs кадры Mirage дают одинаковые оценки ----------
if path.exists():
    import os, tempfile, live_scorer
    from live_scorer import Scorer, STAGE2_AFTER

    def run(feed):
        d = tempfile.mkdtemp()
        sc = Scorer(replay=True, live_db=Path(d) / "live.db", dev_db=Path(d) / "dev.sqlite")
        last = 0.0
        for item in feed:
            sc.on_message(item)
            if sc.clock - last >= 1:
                sc.tick(); last = sc.clock
        sc.clock += STAGE2_AFTER + 1
        sc.tick(); sc.flush_all()
        rows = sc.db.execute("select mint, round(score, 9), level, reasons from scores order by mint").fetchall()
        st = dict(sc.st)
        sc.db.close()
        return rows, st

    def logs_feed():
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield line

    def mirage_feed():
        import base64 as b64
        for line in logs_feed():
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("method") != "logsNotification":
                continue
            r = msg["params"]["result"]; v = r["value"]
            groups = []
            for raw, outer in raw_events_from_logs(v.get("logs") or []):
                if groups and groups[-1][0] == outer:
                    groups[-1][1].append(raw)
                else:
                    groups.append((outer, [raw]))
            fr = frame(r["context"]["slot"], b58decode(v["signature"]), groups, failed=v.get("err") is not None)
            yield json.dumps({"m": b64.b64encode(fr).decode()})     # как пишет --record в режиме mirage

    a_rows, a_st = run(logs_feed())
    b_rows, b_st = run(mirage_feed())
    assert len(a_rows) > 20, len(a_rows)
    assert a_rows == b_rows, (len(a_rows), len(b_rows), [x for x in zip(a_rows, b_rows) if x[0] != x[1]][:3])
    for k in ("creates", "trades", "scored", "red", "yellow"):
        assert a_st[k] == b_st[k], (k, a_st[k], b_st[k])
    print(f"скорер: {len(a_rows):,} оценок совпали (🔴 {a_st['red']}, 🟡 {a_st['yellow']}), "
          f"запусков {a_st['creates']:,}, сделок {a_st['trades']:,}")
print("ВСЁ ОК")
