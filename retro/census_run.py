"""python retro/census_run.py 2026-09-01 [performance]  -> dune_out/census_2026-09-01.csv  (~1 кредит, ~40 с)
    python retro/census_run.py 2026-08-19 2026-09-18      -> все дни диапазона подряд"""
import re, sys
from pathlib import Path
from datetime import datetime, timedelta, timezone
sys.path.insert(0, str(Path(__file__).parent))
from dq import run

fmt = lambda d: d.strftime("%Y-%m-%d %H:%M:%S UTC")
SQL = (Path(__file__).parent / "census.sql").read_text(encoding="utf-8")


def one(day, perf="medium"):
  p = {
      "D0": fmt(day), "D1": fmt(day + timedelta(days=1)), "D3": fmt(day + timedelta(days=3)),
      "H30": fmt(day - timedelta(minutes=30)), "H30E": fmt(day + timedelta(days=1, minutes=30)),
      "P30": fmt(day - timedelta(days=30)),
  }
  sql = SQL
  for k, v in p.items():
      sql = sql.replace("{" + k + "}", v)
  rows = run(sql, f"census_{day:%Y-%m-%d}", perf)
  print(f"{day:%Y-%m-%d}: {None if rows is None else len(rows)} запусков")


if __name__ == "__main__":
    parse = lambda s: datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    a = parse(sys.argv[1])
    if len(sys.argv) > 2 and re.match(r"\d{4}-\d\d-\d\d$", sys.argv[2]):
        b = parse(sys.argv[2])
        while a <= b:
            one(a); a += timedelta(days=1)
    else:
        one(a, sys.argv[2] if len(sys.argv) > 2 else "medium")
