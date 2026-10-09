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

# --- стоп-кран по кредитам ---------------------------------------------------
# Бюджет одного «запуска» — BUDGET кредитов от точки отсчёта в dune_out/_budget.json.
# Упёрлись -> отказ до ручного продления: python retro/dq.py --budget-extend
BUDGET = 200
BUDGET_FILE = OUT / "_budget.json"


def credits_used():
    r = requests.post(f"{API}/usage", headers=H, json={}, timeout=30)
    r.raise_for_status()
    bp = r.json()["billing_periods"][0]
    return float(bp["credits_used"]), float(bp["credits_included"])


def _budget():
    if BUDGET_FILE.exists():
        return json.loads(BUDGET_FILE.read_text(encoding="utf-8"))
    used, _ = credits_used()
    b = {"base": used, "limit": BUDGET}
    BUDGET_FILE.write_text(json.dumps(b), encoding="utf-8")
    return b


def spent_report():
    """Потрачено в запуске: максимум из счётчика Dune (запаздывает) и локальной суммы execution_cost_credits."""
    b = _budget(); used, incl = credits_used()
    return max(used - b["base"], b.get("local", 0.0)), b["limit"], used, incl


def _add_local(cr, dp):
    b = _budget(); b["local"] = b.get("local", 0.0) + cr; b["datapoints"] = b.get("datapoints", 0) + dp
    BUDGET_FILE.write_text(json.dumps(b), encoding="utf-8")


def _check():
    spent, limit, used, incl = spent_report()
    if spent >= limit:
        sys.exit(f"[STOP] бюджет запуска исчерпан: {spent:.0f} из {limit} кредитов "
                 f"(в цикле {used:.0f}/{incl:.0f}). Нужно разрешение Павла, потом --budget-extend")
    return spent, limit


def run(sql, name=None, performance=None, timeout_s=1500, quiet=False):
    before, limit = _check()
    r = requests.post(f"{API}/sql/execute", headers=H, json={"sql": sql, **({"performance": performance} if performance else {})}, timeout=60)
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
    md = st.get("result_metadata") or {}
    _add_local(float(st.get("execution_cost_credits") or 0), int(md.get("datapoint_count") or 0))
    print(f"-- выполнено: {st.get('execution_cost_credits', 0):.2f} кр, {md.get('total_row_count')} строк, "
          f"{md.get('datapoint_count')} датапоинтов")
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
    after, _, used, incl = spent_report()
    with open(OUT / "_spend.log", "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t{name or '-'}\t{len(rows)}\t{after-before:.1f}\t{after:.1f}\n")
    print(f"-- {len(rows)} rows, {time.time()-t0:.0f}s | запрос {after-before:.1f} кр | запуск {after:.0f}/{limit} | цикл {used:.0f}/{incl:.0f}")
    if name:
        cols = cols or (list(rows[0]) if rows else [])
        with open(OUT / f"{name}.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore"); w.writeheader(); w.writerows(rows)
    return rows


def submit(sql, name, performance=None):
    """Отправить запрос и выйти: для оболочек, где процесс живёт меньше, чем считает Dune.
    Забрать результат: python retro/dq.py --collect <name> (можно звать много раз)."""
    before, limit = _check()
    r = requests.post(f"{API}/sql/execute", headers=H, json={"sql": sql, **({"performance": performance} if performance else {})}, timeout=60)
    if r.status_code >= 400:
        print("ERR submit", r.status_code, r.text[:500]); return None
    eid = r.json()["execution_id"]
    (OUT / f"_pending_{name}.json").write_text(json.dumps({"eid": eid, "before": before, "t0": time.time()}), encoding="utf-8")
    print(f"-- отправлено: {eid} -> --collect {name}")
    return eid


def collect(name, wait_s=150):
    pf = OUT / f"_pending_{name}.json"
    p = json.loads(pf.read_text(encoding="utf-8"))
    eid, t_end = p["eid"], time.time() + wait_s
    while True:
        st = requests.get(f"{API}/execution/{eid}/status", headers=H, timeout=60).json()
        s = st.get("state")
        if s == "QUERY_STATE_COMPLETED":
            break
        if s in ("QUERY_STATE_FAILED", "QUERY_STATE_CANCELLED", "QUERY_STATE_EXPIRED"):
            print("ERR", s, json.dumps(st.get("error") or st, ensure_ascii=False)[:1500]); pf.replace(OUT / f"_done_{name}.json"); return None
        if time.time() > t_end:
            print(f"-- ещё считается ({s}), {time.time()-p['t0']:.0f} с с отправки"); return None
        time.sleep(3)
    md = st.get("result_metadata") or {}
    _add_local(float(st.get("execution_cost_credits") or 0), int(md.get("datapoint_count") or 0))
    print(f"-- выполнено: {st.get('execution_cost_credits', 0):.2f} кр, {md.get('total_row_count')} строк, "
          f"{md.get('datapoint_count')} датапоинтов")
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
    after, limit, used, incl = spent_report()
    with open(OUT / "_spend.log", "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t{name}\t{len(rows)}\t{after-p['before']:.1f}\t{after:.1f}\n")
    cols = cols or (list(rows[0]) if rows else [])
    with open(OUT / f"{name}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore"); w.writeheader(); w.writerows(rows)
    pf.replace(OUT / f"_done_{name}.json")
    print(f"-- {len(rows)} строк -> dune_out/{name}.csv | запрос {after-p['before']:.1f} кр | запуск {after:.0f}/{limit} | цикл {used:.0f}/{incl:.0f}")
    return rows


if __name__ == "__main__":
    if sys.argv[1] == "--budget":
        print("запуск: {:.0f} из {} кр | цикл {:.0f}/{:.0f}".format(*spent_report())); sys.exit()
    if sys.argv[1] == "--budget-extend":
        b = _budget(); b["limit"] += BUDGET; BUDGET_FILE.write_text(json.dumps(b), encoding="utf-8")
        print(f"лимит запуска поднят до {b['limit']}"); sys.exit()
    if sys.argv[1] == "--collect":
        collect(sys.argv[2]); sys.exit()
    if sys.argv[1] == "--submit":
        q = sys.argv[2]
        q = open(q[1:], encoding="utf-8").read() if q.startswith("@") else q
        submit(q, sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else None); sys.exit()
    sql = sys.argv[1]
    if sql.startswith("@"):
        sql = open(sql[1:], encoding="utf-8").read()
    name = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] != "-" else None
    perf = sys.argv[3] if len(sys.argv) > 3 else None
    rows = run(sql, name, perf)
    if rows is not None:
        for r in rows[:int(os.environ.get("SHOW", 60))]:
            print(json.dumps(r, ensure_ascii=False, default=str)[:400])
