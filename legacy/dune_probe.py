# -*- coding: utf-8 -*-
"""
dune_probe.py — шаг 0 ретроспективы: что лежит на Dune по pump.fun

    python dune_probe.py

1. Схема: все таблицы в схемах pump* и их колонки     -> dune_out/schema.csv
2. По 3 строки из таблиц create / trade / complete / migrate / buy / sell
                                                       -> dune_out/sample_<table>.csv
3. Сколько запусков и выпусков на AMM в день за 35 дней -> dune_out/daily_<table>.csv

Ключ: .env -> DUNE_API_KEY. На 402 (кредиты кончились) скрипт останавливается.
"""
import csv, json, os, re, sys, time
from pathlib import Path

import requests

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).parent
OUT = HERE / "dune_out"
OUT.mkdir(exist_ok=True)
API = "https://api.dune.com/api/v1"
MAX_SAMPLES = 10          # не больше стольких таблиц сэмплируем — экономим кредиты
SPENT = [0.0]


def load_key():
    k = os.environ.get("DUNE_API_KEY")
    if k:
        return k
    env = HERE / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("DUNE_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit("[FATAL] DUNE_API_KEY нет в .env")


H = {"X-Dune-Api-Key": load_key()}


def run_sql(sql, name, performance="medium", timeout_s=900):
    """Выполнить SQL, дождаться, забрать все строки, сохранить в dune_out/<name>.csv"""
    r = requests.post(f"{API}/sql/execute", headers=H,
                      json={"sql": sql, "performance": performance}, timeout=60)
    if r.status_code == 402:
        sys.exit("[STOP] кредиты Dune кончились (402)")
    if r.status_code in (401, 403):
        sys.exit(f"[FATAL] Dune отверг ключ: {r.status_code} {r.text[:200]}")
    if r.status_code >= 400:
        print(f"[ERR] {name}: {r.status_code} {r.text[:300]}")
        return None
    eid = r.json()["execution_id"]

    t0, st = time.time(), {}
    while True:
        st = requests.get(f"{API}/execution/{eid}/status", headers=H, timeout=60).json()
        state = st.get("state")
        if state == "QUERY_STATE_COMPLETED":
            break
        if state in ("QUERY_STATE_FAILED", "QUERY_STATE_CANCELLED", "QUERY_STATE_EXPIRED"):
            err = st.get("error") or st
            print(f"[ERR] {name}: {state} {json.dumps(err, ensure_ascii=False)[:400]}")
            return None
        if time.time() - t0 > timeout_s:
            print(f"[ERR] {name}: не дождался за {timeout_s}s, execution_id={eid}")
            return None
        time.sleep(3)

    rows, cols, offset = [], None, 0
    while True:
        res = requests.get(f"{API}/execution/{eid}/results", headers=H,
                           params={"limit": 10000, "offset": offset}, timeout=120).json()
        result = res.get("result") or {}
        cols = cols or (result.get("metadata") or {}).get("column_names")
        rows += result.get("rows") or []
        nxt = res.get("next_offset")
        if nxt is None or nxt == offset:
            break
        offset = nxt

    cost = st.get("execution_cost_credits")
    if isinstance(cost, (int, float)):
        SPENT[0] += cost
    cols = cols or (list(rows[0].keys()) if rows else [])
    with open(OUT / f"{name}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"[OK] {name}: {len(rows)} строк, {time.time()-t0:.0f}s"
          + (f", кредитов: {cost}" if cost is not None else ""))
    return rows


def main():
    # 1. схема
    schema = run_sql("""
        SELECT table_schema, table_name, column_name, data_type
        FROM information_schema.columns
        WHERE table_schema LIKE 'pump%'
        ORDER BY table_schema, table_name, ordinal_position
    """, "schema")
    if not schema:
        sys.exit("[STOP] схему получить не удалось — пришли вывод целиком")

    tables = {}
    for r in schema:
        tables.setdefault((r["table_schema"], r["table_name"]), []).append(r["column_name"])

    print(f"\nсхем/таблиц: {len({s for s, _ in tables})} / {len(tables)}")
    for (s, t), cols in sorted(tables.items()):
        print(f"  {s}.{t}  ({len(cols)} кол.)")

    # 2. сэмплы интересных таблиц
    pat = re.compile(r"create|trade|complete|migrat|withdraw|_buy|_sell", re.I)
    picked = [(s, t) for (s, t) in sorted(tables) if pat.search(t)][:MAX_SAMPLES]
    print(f"\nсэмплирую {len(picked)} таблиц")
    time_col = {}
    for s, t in picked:
        tc = next((c for c in tables[(s, t)] if c.endswith("block_time")), None)
        if not tc:
            print(f"  [skip] {s}.{t}: нет колонки *block_time")
            continue
        time_col[(s, t)] = tc
        run_sql(f"SELECT * FROM {s}.{t} WHERE {tc} > now() - interval '3' hour LIMIT 3",
                f"sample_{s}__{t}")

    # 3. объёмы по дням: запуски и выпуски на AMM
    for (s, t), tc in time_col.items():
        if re.search(r"create|complete|migrat|withdraw", t, re.I):
            run_sql(f"""SELECT date_trunc('day', {tc}) AS day, count(*) AS n
                        FROM {s}.{t}
                        WHERE {tc} > now() - interval '35' day
                        GROUP BY 1 ORDER BY 1""", f"daily_{s}__{t}")

    print(f"\nготово. потрачено кредитов (если Dune отдаёт): {SPENT[0]:g}")
    print(f"файлы: {OUT}")


if __name__ == "__main__":
    main()
