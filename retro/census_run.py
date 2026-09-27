"""Урезанная перепись для обучения (по умолчанию): только колонки train_live и запуски, куда розница
занесла >= 1 SOL -> dune_out/censusL_<day>.csv, ~6 кр/день. Полная (--full, ~31 кр/день из-за выгрузки
1,4 млн датапоинтов) -> dune_out/census_<day>.csv; она нужна devdb --seed и analyze.py.

python retro/census_run.py 2026-09-01 [performance]  -> dune_out/census_2026-09-01.csv  (~1 кредит, ~40 с)
    python retro/census_run.py 2026-08-19 2026-09-18      -> все дни диапазона подряд"""
import re, sys
from pathlib import Path
from datetime import datetime, timedelta, timezone
sys.path.insert(0, str(Path(__file__).parent))
from dq import run

fmt = lambda d: d.strftime("%Y-%m-%d %H:%M:%S UTC")
SQL = (Path(__file__).parent / "census.sql").read_text(encoding="utf-8")


LEAN_COLS = ["mint", "c_time", "g_time", "n_tr", "peak_mc", "s0_supply", "dev_supply", "s2_supply", "s0_buyers",
             "multi_tx", "max_wal_per_tx", "dev_prev_30d", "dev_prev_grad", "sym_before_other", "mayhem",
             "n_wal_10s", "net_supply_10s", "rest_n", "rest_spent", "rest_net", "rest_losers50"]
FULL = "--full" in sys.argv
if FULL:
    sys.argv.remove("--full")


def lean(sql):
    tail = "select *\nfrom cr\n"
    assert tail in sql, "census.sql: не нашёл финальный select *"
    return sql.replace(tail, "select " + ", ".join(LEAN_COLS) + "\nfrom cr\n", 1) + \
        "where n_tr is not null and peak_mc > 0 and rest_n > 0 and rest_spent >= 1\n"


def one(day, perf=None):
  from dq import OUT
  if not FULL and (OUT / f"censusL_{day:%Y-%m-%d}.csv").exists():
      print(f"{day:%Y-%m-%d}: уже есть, пропуск"); return
  p = {
      "D0": fmt(day), "D1": fmt(day + timedelta(days=1)), "D3": fmt(day + timedelta(days=3)),
      "H30": fmt(day - timedelta(minutes=30)), "H30E": fmt(day + timedelta(days=1, minutes=30)),
      "P30": fmt(day - timedelta(days=30)),
  }
  sql = SQL
  for k, v in p.items():
      sql = sql.replace("{" + k + "}", v)
  if not FULL:
      sql = lean(sql)
  rows = run(sql, f"{'census' if FULL else 'censusL'}_{day:%Y-%m-%d}", perf)
  print(f"{day:%Y-%m-%d}: {None if rows is None else len(rows)} запусков")


if __name__ == "__main__":
    parse = lambda s: datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    a = parse(sys.argv[1])
    if len(sys.argv) > 2 and re.match(r"\d{4}-\d\d-\d\d$", sys.argv[2]):
        b = parse(sys.argv[2])
        while a <= b:
            one(a); a += timedelta(days=1)
    else:
        one(a, sys.argv[2] if len(sys.argv) > 2 else None)
