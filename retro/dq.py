"""dq.py — Dune SQL из консоли: python retro/dq.py "<sql>" [name] [performance]  (sql из файла: @file.sql)
Результат с именем пишется в dune_out/<name>.csv. Ключ: env DUNE_API_KEY или .env в корне проекта."""
import csv, json, os, time
import requests
import sys
from pathlib import Path
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "dune_out"
OUT.mkdir(exist_ok=True)


def _key():
    k = os.environ.get("DUNE_API_KEY")
    if k:
        return k
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("DUNE_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit("[FATAL] DUNE_API_KEY нет ни в окружении, ни в .env")


API = "https://api.dune.com/api/v1"
H = {"X-Dune-Api-Key": _key()}


def run(sql, name=None, performance="medium", timeout_s=1500, quiet=False):
    r = requests.post(f"{API}/sql/execute", headers=H, json={"sql": sql, "performance": performance}, timeout=60)
    if r.status_code >= 400:
        print("ERR submit", r.status_code, r.text[:500]); return None
    eid = r.json()["execution_id"]
    t0 = time.time()
    while True:
        st = requests.get(f"{API}/execution/{eid}/status", headers=H, timeout=60).json()
        s = st.get("state")
        if s == "QUERY_STATE_COMPLETED":
            break
        if s in ("QUERY_STATE_FAILED", "QUERY_STATE_CANCELLED", "QUERY_STATE_EXPIRED"):
            print("ERR", s, json.dumps(st.get("error") or st, ensure_ascii=False)[:1500]); return None
        if time.time() - t0 > timeout_s:
            print("TIMEOUT", eid); return None
        time.sleep(3)
    rows, cols, off = [], None, 0
    while True:
        res = requests.get(f"{API}/execution/{eid}/results", headers=H, params={"limit": 25000, "offset": off}, timeout=180).json()
        rr = res.get("result") or {}
        cols = cols or (rr.get("metadata") or {}).get("column_names")
        rows += rr.get("rows") or []
        nx = res.get("next_offset")
        if nx is None or nx == off:
            break
        off = nx
    cost = {k: v for k, v in st.items() if "cost" in k or "credit" in k}
    if not quiet:
        print(f"-- {len(rows)} rows, {time.time()-t0:.0f}s {cost}")
    if name:
        cols = cols or (list(rows[0]) if rows else [])
        with open(OUT / f"{name}.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore"); w.writeheader(); w.writerows(rows)
    return rows


if __name__ == "__main__":
    sql = sys.argv[1]
    if sql.startswith("@"):
        sql = open(sql[1:], encoding="utf-8").read()
    name = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] != "-" else None
    perf = sys.argv[3] if len(sys.argv) > 3 else "medium"
    rows = run(sql, name, perf)
    if rows is not None:
        for r in rows[:int(os.environ.get("SHOW", 60))]:
            print(json.dumps(r, ensure_ascii=False, default=str)[:400])
