"""python retro/funding_run.py 2026-09-01 [2026-09-07]  -> dune_out/funding_<день>.csv   (ДОРОГО: ~120 кредитов/день)"""
import re, sys
from pathlib import Path
from datetime import datetime, timedelta, timezone
sys.path.insert(0, str(Path(__file__).parent))
from dq import run

fmt = lambda d: d.strftime("%Y-%m-%d %H:%M:%S UTC")
SQL = (Path(__file__).parent / "funding.sql").read_text(encoding="utf-8")


def one(day):
    p = {"D0": day, "D1": day + timedelta(days=1), "D3": day + timedelta(days=3),
         "D1P": day + timedelta(days=1, minutes=10), "F0": day - timedelta(days=14), "W0": day - timedelta(days=7)}
    sql = SQL
    for k in sorted(p, key=len, reverse=True):
        sql = sql.replace("{" + k + "}", fmt(p[k]))
    rows = run(sql, f"funding_{day:%Y-%m-%d}")
    print(f"{day:%Y-%m-%d}: {None if rows is None else len(rows)} строк")


if __name__ == "__main__":
    parse = lambda s: datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    a = parse(sys.argv[1]); b = parse(sys.argv[2]) if len(sys.argv) > 2 else a
    while a <= b:
        one(a); a += timedelta(days=1)
