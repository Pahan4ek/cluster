"""python retro/analyze.py  (нужны pandas, scikit-learn)
Ретроспектива: классы исходов, стабильность признаков по неделям, проверка на отложенной неделе."""
import glob
import numpy as np
import pandas as pd
import sys
from pathlib import Path
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "dune_out"
OUT.mkdir(exist_ok=True)

pd.set_option("display.width", 200); pd.set_option("display.max_columns", 30)

d = pd.concat([pd.read_csv(f) for f in sorted(glob.glob(str(OUT / "census_2026-*.csv*")))], ignore_index=True)
d["c_time"] = pd.to_datetime(d.c_time.str.replace(" UTC", ""), utc=True)
d = d.drop_duplicates("mint")
non_sol = (d.peak_mc == 0) & d.n_tr.notna()
print(f"исключено запусков с котировкой не в SOL: {non_sol.sum():,}")
d = d[~non_sol].copy()
f = lambda c: d[c].fillna(0)

d["grad"] = d.g_time.notna()
d["dev_x"] = f("dev_net") + f("creator_fees")
d["nondev_s0"] = (f("s0_supply") - f("dev_supply")).clip(lower=0)
d["comp_before"] = f("sym_before_other")            # известно в момент запуска
d["comp_any"] = f("sym_before_other") + f("sym_after_other")  # только для метки
d["rest_roi"] = f("rest_net") / d.rest_spent.replace(0, np.nan)
d["dev_grad_rate"] = f("dev_prev_grad") / f("dev_prev_30d").replace(0, np.nan)
d["week"] = ((d.c_time - pd.Timestamp("2026-08-19", tz="UTC")).dt.days // 7).clip(upper=3)


def cls(r):
    if pd.isna(r.n_tr): return "0 без сделок"
    if r.grad: return "5 выход на AMM"
    if (r.rest_n or 0) == 0 or (r.rest_spent or 0) < 0.1: return "1 мертворождённый"
    if r.dev_x >= 0.3 and r.dev_x >= 0.3 * (-(r.rest_net or 0)): return "2 раг девом"
    if r.comp_any > 0 and r.sib_graduated == 1: return "3 проиграл ПвП"
    return "4 органическая смерть"


d["cls"] = d.apply(cls, axis=1)

print(f"запусков: {len(d):,}  дней: {d.c_time.dt.date.nunique()}  {d.c_time.min():%d.%m}–{d.c_time.max():%d.%m}\n")
ct = pd.crosstab(d.cls, d.week, normalize="columns").round(3)
ct["всего"] = d.cls.value_counts(normalize=True).round(3)
ct["n"] = d.cls.value_counts()
print("доли классов по неделям\n", ct, "\n")

x = d[d.cls.str[0].isin(list("2345"))].copy()          # были внешние покупатели
dead = x[~x.grad]


def bucket(col, bins, frame=x):
    b = pd.cut(frame[col].fillna(0), bins=bins, include_lowest=True)
    out = frame.groupby([b, "week"], observed=True).apply(
        lambda s: pd.Series({"n": len(s), "grad": s.grad.mean(),
                             "rug": (s.cls == "2 раг девом").mean(),
                             "roi_dead": s.loc[~s.grad, "rest_roi"].median()}), include_groups=False)
    piv = out.unstack("week")
    print(f"\n== {col}  (grad / rug / медиана ROI розницы на мёртвых, по неделям 0-3)")
    print(pd.concat({k: piv[k].round(3) for k in ["n", "grad", "rug", "roi_dead"]}, axis=1).to_string())


bucket("dev_prev_30d", [0, .5, 5, 50, 500, 1e7])
bucket("dev_supply", [0, .001, .03, .1, .3, 1])
bucket("nondev_s0", [0, .001, .03, .1, .3, 1])
bucket("s0_buyers", [0, .5, 2, 5, 100])
bucket("multi_tx", [0, .5, 1.5, 100])
bucket("copaid_w5", [0, .5, 2, 100])
bucket("comp_before", [0, .5, 3, 20, 1e4])
bucket("mayhem", [-.5, .5, 1.5])

# --- проверка на отложенной неделе: только признаки, известные в первые секунды ---
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score

FEAT = ["dev_supply", "nondev_s0", "s2_supply", "s0_buyers", "multi_tx", "max_wal_per_tx", "copaid_w5",
        "dev_prev_30d", "dev_prev_grad", "dev_grad_rate", "comp_before", "sym_same_creator", "mayhem",
        "n_wal_10s", "net_supply_10s"]
# sym_same_creator считает и будущие запуски дева в ±30 мин — оставляю только прошлые? нет данных -> убираю
FEAT.remove("sym_same_creator")
for c in FEAT:
    x[c] = x[c].fillna(0).astype(float)

tr, te = x[x.week < 3], x[x.week == 3]
print(f"\n\nобучение: недели 0-2 ({len(tr):,})  проверка: неделя 3 ({len(te):,})")
targets = {
    "выход на AMM": lambda s: s.grad,
    "раг девом": lambda s: s.cls == "2 раг девом",
    "розница на мёртвых теряет >50%": lambda s: (~s.grad) & (s.rest_roi < -0.5),
}
for name, fn in targets.items():
    m = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, random_state=0)
    m.fit(tr[FEAT], fn(tr))
    p = m.predict_proba(te[FEAT])[:, 1]
    y = fn(te).values
    auc = roc_auc_score(y, p)
    top = np.argsort(-p)[: max(1, len(p) // 10)]
    print(f"  {name:32s} база {y.mean():.3f} | AUC {auc:.3f} | в топ-10% скора: {y[top].mean():.3f} (lift {y[top].mean()/y.mean():.1f}x)")
    # что даёт каждый признак отдельно на проверке (AUC одиночного признака)
    single = {c: roc_auc_score(y, te[c]) for c in FEAT if te[c].nunique() > 1}
    best = sorted(single.items(), key=lambda kv: -abs(kv[1] - .5))[:4]
    print("     сильнейшие одиночные:", ", ".join(f"{c} {a:.2f}" for c, a in best))

