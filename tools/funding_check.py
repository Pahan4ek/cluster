"""funding_check.py — разбор одного запуска: кто из ранних покупателей кошельки дева, и кто сколько заработал.
    python tools/funding_check.py <mint> [<mint> ...] [--minutes 5] [--max 40]
Берёт всю историю минта через RPC (Solami, иначе Helius), находит дева и ранних покупателей,
ищет их пополнителей (live/funding.py) и делит PnL: дев / кластер дева / прочие группы / остальные.
Ничего не пишет в базы. Ключи не печатает.
"""
import sys, time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass
from live import funding
from live.pump_events import events_from_logs, events_from_tx

LAMPORTS = 1e9


def arg(k, d):
    return type(d)(sys.argv[sys.argv.index(k) + 1]) if k in sys.argv else d


def short(a):
    return f"{a[:4]}…{a[-4:]}" if a else "—"


def history(rpc, mint, pages=10):
    sigs, before = [], None
    for _ in range(pages):
        page = rpc.sigs(mint, before=before, limit=1000)
        if not page:
            break
        sigs += page
        before = page[-1]["signature"]
        if len(page) < 1000:
            break
    return [s for s in reversed(sigs) if not s.get("err")]       # от старых к новым


def check(rpc, mint, minutes, max_buyers, cex):
    t0 = time.time()
    sigs = history(rpc, mint)
    with ThreadPoolExecutor(8) as ex:
        txs = list(ex.map(lambda s: rpc.tx(s["signature"]), sigs))
    create, trades = None, []
    for s, tx in zip(sigs, txs):
        evs = events_from_tx(tx)
        if not evs and tx and tx.get("meta"):        # запасной путь: события из логов
            evs = events_from_logs(tx["meta"].get("logMessages") or [])
        for ev, outer in evs:
            if ev["mint"] != mint:
                continue
            if ev["kind"] == "create" and create is None:
                create = (ev, s["signature"])
            elif ev["kind"] == "trade":
                trades.append((tx.get("slot") or s.get("slot"), ev["timestamp"], ev["user"], ev["is_buy"],
                               ev["sol_amount"] / LAMPORTS, s["signature"]))
    if not create:
        none = sum(1 for t in txs if t is None)
        print(f"\n=== {mint}: создание не найдено ({len(sigs)} транзакций, не получено {none}, "
              f"сделок {len(trades)}; RPC запросов {rpc.calls}, ошибок {rpc.errors}, последняя: {rpc.last_err})")
        for s, tx in list(zip(sigs, txs))[:2]:                 # как выглядит транзакция — для разбора
            if not tx:
                continue
            msg = tx["transaction"]["message"]
            top = [ix.get("programId") for ix in msg.get("instructions") or []]
            inner = [(ix.get("programId"), len(ix.get("data") or ""), sorted(ix)[:6])
                     for g in tx["meta"].get("innerInstructions") or [] for ix in g.get("instructions") or []][:6]
            logs = [l[:60] for l in (tx["meta"].get("logMessages") or []) if "Program data" in l][:3]
            print(f"  {s['signature'][:10]}… version={tx.get('version')} top={top}\n    inner={inner}\n    logs={logs}")
        return
    cev, csig = create
    dev, ts0 = cev["creator"], cev["timestamp"]
    trades.sort(key=lambda t: (t[0] or 0, t[1]))
    buyers = {}
    for slot, ts, user, is_buy, sol, sig in trades:
        if not is_buy or user == dev or ts - ts0 > minutes * 60:
            continue
        if user not in buyers:
            if len(buyers) >= max_buyers:
                continue
            buyers[user] = [sig, 0.0]
        buyers[user][1] += sol
    r = funding.analyse(rpc, dev, {w: tuple(v) for w, v in buyers.items()}, dev_before_sig=csig, cex=cex)
    pnl = defaultdict(lambda: [0.0, 0.0])                  # кошелёк -> [вложил, вынес]
    for slot, ts, user, is_buy, sol, sig in trades:
        pnl[user][0 if is_buy else 1] += sol

    def total(ws):
        sp = sum(pnl[w][0] for w in ws); rc = sum(pnl[w][1] for w in ws)
        return sp, rc, (rc - sp) / sp if sp else 0.0
    cl, gw = r["cluster"], r["group_wallets"]
    rest_early = set(buyers) - cl - gw
    side = {w for w, (sp, rc) in pnl.items() if sp < 0.001 and rc > 0 and w != dev}   # продали, не купив
    late = set(pnl) - set(buyers) - {dev} - side
    di = r["dev_info"]
    when = datetime.fromtimestamp(ts0, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"\n=== ${cev['symbol']} ({cev['name']}) {mint}")
    print(f"создан {when}, транзакций {len(sigs):,}, сделок {len(trades):,}")
    age = f", за {(ts0 - di['fund_ts']) / 3600:.1f} ч до запуска" if di["fund_ts"] else ""
    print(f"дев {short(dev)}: пополнен с {short(di['funder'])} на {di['lamports'] / LAMPORTS:.2f} SOL{age}"
          + (f" ({cex[di['funder']]})" if di["funder"] in cex else ""))
    if di["funder"]:
        try:
            from live.devdb import DevDB
            db = DevDB(ROOT / "devdb.sqlite")
            n30, g30 = db.history(di["funder"], ts0)
            print(f"  пополнитель дева сам запускал токены: {n30} за 30 дней, выходов на AMM {g30}")
        except Exception as e:
            print(f"  (история пополнителя недоступна: {type(e).__name__})")
    print(f"ранних покупателей (первые {minutes} мин): {r['n']}, свежих кошельков (≤2 транзакций до покупки): {r['fresh']}")
    print(f"  кластер дева: {len(cl)} кошельков, {r['share_sol']:.0%} SOL ранних покупок")
    for g in r["groups"]:
        print(f"  группа без связи с девом: {len(g['wallets'])} кошельков, общий пополнитель {short(g['funder'])}")
    fun = defaultdict(list)
    for w, i in r["infos"].items():
        fun[i["funder"]].append(w)
    top = sorted(((f, ws) for f, ws in fun.items() if f), key=lambda x: -len(x[1]))[:5]
    print("  чаще всего пополняли: " + ", ".join(
        f"{short(f)}{' (дев)' if f == dev else ' (пополнитель дева)' if f == di['funder'] else ''}"
        f"{' [' + cex[f] + ']' if f in cex else ' [хаб]' if f in r['hubs'] else ''} ×{len(ws)}" for f, ws in top))
    print("PnL по всей истории (SOL вложено → вынесено, ROI):")
    for name, ws in [("дев", {dev}), ("кластер дева", cl), ("другие группы", gw),
                     ("остальные ранние", rest_early), ("продали без покупки", side),
                     ("поздние (не проверялись)", late)]:
        if ws:
            sp, rc, roi = total(ws)
            print(f"  {name:26s} {len(ws):4d} кошельков  {sp:9.2f} → {rc:9.2f}  " + (f"{roi:+.0%}" if sp else "—"))
    print(f"запросов RPC {rpc.calls:,}, ошибок {rpc.errors} (последняя: {rpc.last_err}), {time.time() - t0:.0f} с")


if __name__ == "__main__":
    mints = [a for a in sys.argv[1:] if not a.startswith("--") and len(a) > 30]
    if not mints:
        sys.exit(__doc__)
    rpc = funding.RPC(rps=arg("--rps", 40.0))
    cex = funding.load_cex()
    for m in mints:
        try:
            check(rpc, m, arg("--minutes", 5), arg("--max", 40), cex)
        except Exception as e:
            print(f"\n=== {m}: ошибка {type(e).__name__}: {str(e).split('api_key=')[0].split('api-key=')[0]}")
