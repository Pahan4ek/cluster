# -*- coding: utf-8 -*-
"""live_report.py — live backtest from live.db: how many launches were scored, levels, decision lag,
and how levels line up with outcomes (closed launches where retail put in >= 1 SOL).
Next to it: the held-out Dune week from model/live_model.json.

    python live_report.py
"""
import json
import shutil
import sqlite3
import statistics as st
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent
src = ROOT / "live.db"
if not src.exists():
    sys.exit("no live.db yet — the scorer has not run")
# read a copy so the running scorer is not disturbed
tmp = Path(tempfile.mkdtemp()) / "live.db"
for suf in ("", "-wal", "-shm"):
    if (ROOT / f"live.db{suf}").exists():
        shutil.copy(ROOT / f"live.db{suf}", tmp.parent / f"live.db{suf}")
c = sqlite3.connect(tmp)
q = lambda s, *a: c.execute(s, a).fetchall()

since = int(sys.argv[1]) if len(sys.argv) > 1 else 0          # unix time: report only launches after it
n, a, b = q("select count(*), min(c_ts), max(c_ts) from scores where c_ts >= ?", since)[0]
if not n:
    sys.exit("no scores yet")
hrs = max((b - a) / 3600, 1e-9)
lv = dict(q("select level, count(*) from scores where c_ts >= ? group by 1", since))
lags = sorted(r[0] for r in q("select lag_s from scores where c_ts >= ?", since))
print(f"scored launches: {n:,} over {hrs:.1f} h ({n/hrs:,.0f}/h)")
print("levels: " + " | ".join(f"{k} {lv.get(k,0):,} ({lv.get(k,0)/n:.1%})" for k in ("red", "yellow", "green", "gap")))
print(f"decision lag after creation seen: median {st.median(lags):.1f} s, p95 {lags[int(len(lags)*.95)]:.1f} s")
s2 = q("select count(*), sum(flag is not null) from stage2 s join scores using(mint) where c_ts >= ?", since)[0]
print(f"5-minute stage: {s2[0]:,}, flag 'all early buyers called pump directly': {s2[1] or 0}")

rows = q("""select s.level, o.graduated, o.rest_spent, o.rest_net
            from scores s join outcomes o using(mint) where s.c_ts >= ? and o.closed != 'gap'""", since)
print(f"\nclosed outcomes: {len(rows):,} (launch quiet for 1 h or older than 24 h; stream gaps excluded)")
agg = defaultdict(lambda: {"n": 0, "money": 0, "trap": 0, "sp": 0.0, "net": 0.0, "grad": 0})
for lvl, g, sp, net in rows:
    x = agg[lvl]; x["n"] += 1; x["grad"] += g
    if sp >= 1:
        x["money"] += 1; x["sp"] += sp; x["net"] += net
        x["trap"] += int((not g) and net / sp < -0.5)
print("LIVE      | launches with ≥1 SOL retail | trap rate | retail ROI (SOL in) | graduated")
for lvl in ("red", "yellow", "green"):
    x = agg[lvl]
    if not x["money"]:
        continue
    print(f"  {lvl:7s} | {x['money']:>27,} | {x['trap']/x['money']:9.1%} | {x['net']/x['sp']:+7.1%} ({x['sp']:,.0f}) | {x['grad']}")

m = json.loads((ROOT / "model" / "live_model.json").read_text(encoding="utf-8"))
roi, h = m["roi_by_level"], m["holdout"]
n10, n2 = h["n"] // 10, h["n"] // 50
trap = {"red": h["top2"], "yellow": (h["top10"] * n10 - h["top2"] * n2) / (n10 - n2),
        "green": (h["base"] * h["n"] - h["top10"] * n10) / (h["n"] - n10)}
print("DUNE week | (held out, never seen in training)")
for lvl in ("red", "yellow", "green"):
    print(f"  {lvl:7s} | {roi[lvl]['n']:>27,} | {trap[lvl]:9.1%} | {roi[lvl]['retail_roi']:+7.1%} ({roi[lvl]['retail_sol']:,.0f}) |")
if sum(a["money"] for a in agg.values()) < 2000:
    print("\nfew outcomes yet — need 1-2 days of continuous running before reading much into the live rows")
