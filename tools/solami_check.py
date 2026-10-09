"""solami_check.py — проверка Solami перед запуском скорера: ключ, RPC, подписка Mirage, живой поток.
    python tools/solami_check.py [секунд=30]
Ничего не пишет в базы. Ключ берёт из .env (SOLAMI_API_KEY) и никуда его не печатает.
"""
import asyncio, os, sys, time
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
import requests
from live import mirage

KEY = os.environ.get("SOLAMI_API_KEY")
if not KEY:
    sys.exit("[FATAL] нет SOLAMI_API_KEY в .env")
SECS = float(sys.argv[1]) if len(sys.argv) > 1 else 30


def hide(t):
    return str(t).replace(KEY, "<key>")


r = requests.post(mirage.RPC.format(key=KEY), json={"jsonrpc": "2.0", "id": 1, "method": "getSlot"}, timeout=15)
print(f"[RPC] getSlot -> {r.status_code} {hide(r.text[:120])}")

try:
    sid = mirage.ensure_subscription(KEY)
    print(f"[MIRAGE] подписка {sid}")
except Exception as e:
    sys.exit(f"[MIRAGE] {hide(e)}")


async def stream():
    import websockets
    n = tx = ev = creates = trades = 0
    lags, t0 = [], time.time()
    first = None
    async with websockets.connect(mirage.stream_url(KEY, sid), max_size=64 * 2**20) as ws:
        print(f"[MIRAGE] подключился, слушаю {SECS:.0f} с…")
        while time.time() - t0 < SECS:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=2)
            except asyncio.TimeoutError:
                continue
            n += 1
            if isinstance(raw, str):
                print(f"[MIRAGE] текст: {hide(raw)[:200]}")
                continue
            first = first or time.time() - t0
            u = mirage.parse(raw)
            if u[0] != "tx":
                continue
            tx += 1
            for e, _ in u[4]:
                ev += 1
                creates += e["kind"] == "create"
                trades += e["kind"] == "trade"
                lags.append(time.time() - e["timestamp"])
    lags.sort()
    med = lags[len(lags) // 2] if lags else float("nan")
    print(f"[MIRAGE] кадров {n:,}, транзакций {tx:,}, событий pump {ev:,} (создания {creates}, сделки {trades:,}); "
          f"первый кадр через {first or 0:.1f} с; задержка от времени блока: медиана {med:.1f} с")
    if not ev:
        print("[!] событий pump нет — пришли мне вывод, разберёмся")

asyncio.run(stream())
