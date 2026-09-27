# -*- coding: utf-8 -*-
"""live_report.py — сводка по live.db: сколько оценено, уровни, лаг, и как уровни
сходятся с исходами (только закрытые запуски, куда розница занесла >= 1 SOL).

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
    sys.exit("live.db нет — скорер ещё не запускался")
# читаем копию, чтобы не мешать работающему скореру
tmp = Path(tempfile.mkdtemp()) / "live.db"
for suf in ("", "-wal", "-shm"):
    if (ROOT / f"live.db{suf}").exists():
        shutil.copy(ROOT / f"live.db{suf}", tmp.parent / f"live.db{suf}")
c = sqlite3.connect(tmp)
q = lambda s: c.execute(s).fetchall()

n, a, b = q("select count(*), min(c_ts), max(c_ts) from scores")[0]
if not n:
    sys.exit("оценок пока нет")
hrs = max((b - a) / 3600, 1e-9)
lv = dict(q("select level, count(*) from scores group by 1"))
lags = sorted(r[0] for r in q("select lag_s from scores"))
print(f"оценено запусков: {n:,} за {hrs:.1f} ч ({n/hrs:,.0f}/ч)")
print("уровни: " + " | ".join(f"{k} {lv.get(k,0):,} ({lv.get(k,0)/n:.1%})" for k in ("red", "yellow", "green")))
print(f"лаг решения: медиана {st.median(lags):.1f} с, p95 {lags[int(len(lags)*.95)]:.1f} с")
s2 = q("select count(*), sum(flag is not null) from stage2")[0]
print(f"второй этап (5 мин): {s2[0]:,}, флаг «все ранние напрямую в pump»: {s2[1] or 0}")

rows = q("""select s.level, o.graduated, o.rest_spent, o.rest_net
            from scores s join outcomes o using(mint)""")
print(f"\nзакрытых исходов: {len(rows):,} (запуск затих на час или прожил сутки)")
agg = defaultdict(lambda: {"n": 0, "money": 0, "trap": 0, "sp": 0.0, "net": 0.0, "grad": 0})
for lvl, g, sp, net in rows:
    x = agg[lvl]; x["n"] += 1; x["grad"] += g
    if sp >= 1:
        x["money"] += 1; x["sp"] += sp; x["net"] += net
        x["trap"] += int((not g) and net / sp < -0.5)
for lvl in ("red", "yellow", "green"):
    x = agg[lvl]
    if not x["money"]:
        continue
    print(f"  {lvl:6s}: с розницей ≥1 SOL {x['money']:>5,} | ловушек {x['trap']/x['money']:6.1%} "
          f"| ROI денег розницы {x['net']/x['sp']:+6.1%} ({x['sp']:,.0f} SOL) | вышли на AMM {x['grad']}")
print("\nна проверке Dune было: 🔴 ловушек ~32%, ROI −30%; 🟡 ~20%, −18%; 🟢 ~4%, −10%.")
if sum(a["money"] for a in agg.values()) < 2000:
    print("исходов пока мало — выводы делать рано, нужно 2-3 дня непрерывной работы")
