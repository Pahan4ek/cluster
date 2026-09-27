"""python retro/analyze_hidden.py  (pandas, scikit-learn)
Скрытая координация на «чистых» запусках: добавляют ли отпечатки что-то поверх базы."""
import glob, warnings
import numpy as np, pandas as pd
import sys
from pathlib import Path
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "dune_out"
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score
warnings.filterwarnings("ignore")
pd.set_option("display.width", 220); pd.set_option("display.max_columns", 40)


def load(prefix):
    return pd.concat([pd.read_csv(f) for f in sorted(glob.glob(str(OUT / f"{prefix}_2026-*.csv*")))], ignore_index=True).drop_duplicates("mint")


c = load("census"); h = load("hidden"); l = load("late")
d = c.merge(h, on="mint", how="left").merge(l, on="mint", how="left")
d = d[~((d.peak_mc == 0) & d.n_tr.notna())].copy()
d["c_time"] = pd.to_datetime(d.c_time.str.replace(" UTC", ""), utc=True)
d["week"] = ((d.c_time - pd.Timestamp("2026-08-19", tz="UTC")).dt.days // 7).clip(upper=3)
z = lambda col: d[col].fillna(0)
d["grad"] = d.g_time.notna()
d["nondev_s0"] = (z("s0_supply") - z("dev_supply")).clip(lower=0)
d["dev_grad_rate"] = z("dev_prev_grad") / z("dev_prev_30d").replace(0, np.nan)
d["flip_share"] = z("flippers_5m") / d.org_buyers_5m.replace(0, np.nan)
d["wash_share"] = z("rt_vol") / d.tot_vol.replace(0, np.nan)
d["dust_share"] = z("dust_holders") / d.buyers.replace(0, np.nan)
d["l5_roi"] = d.l5_net / d.l5_spent.replace(0, np.nan)
d["l30_roi"] = d.l30_net / d.l30_spent.replace(0, np.nan)
d["e5_roi"] = d.e5_net / d.e5_spent.replace(0, np.nan)

clean = (d.n_tr.notna() & (d.nondev_s0 < 0.03) & (z("multi_tx") == 0) & (z("copaid_w5") == 0)
         & (z("dev_supply") < 0.05) & (z("org_buyers_5m") >= 5) & (z("l5_spent") >= 0.5))
x = d[clean].copy()
x["trap5"] = (~x.grad) & (x.l5_roi < -0.5)
x["trap30"] = (~x.grad) & (x.l30_roi < -0.5) & (x.l30_spent >= 0.5)
x["e5_took"] = (x.e5_net > 0.5) & (x.l5_net < -0.5)   # ранняя «органика» в плюсе за счёт поздних

print(f"всего запусков {len(d):,}; «чистых на вид» с живой активностью и поздней розницей: {len(x):,} ({len(x)/len(d):.1%})")
print(f"среди чистых: выход на AMM {x.grad.mean():.1%} | поздняя розница (после 5 мин) теряет >50%: {x.trap5.mean():.1%} "
      f"| ранние в плюсе, поздние в минусе: {x.e5_took.mean():.1%}")
tot_loss = (-x.l5_net.clip(upper=0)).sum()
print(f"потери поздней розницы на чистых: {tot_loss:,.0f} SOL за месяц; "
      f"из них где ранние в плюсе: {(-x.loc[x.e5_took, 'l5_net'].clip(upper=0)).sum()/tot_loss:.0%}")


def bucket(col, bins):
    b = pd.cut(x[col], bins=bins, include_lowest=True)
    g = x.groupby([b, "week"], observed=True).agg(n=("mint", "size"), trap5=("trap5", "mean"),
                                                   took=("e5_took", "mean"), grad=("grad", "mean"))
    u = g.unstack("week")
    print(f"\n== {col}")
    print(pd.concat({k: u[k].round(3) for k in ["n", "trap5", "took", "grad"]}, axis=1).to_string())


bucket("dup_size_share", [0, .001, .15, .3, 1])
bucket("top_prog_share", [0, .5, .75, .99, 1])
bucket("direct_share", [0, .2, .5, .8, 1])
bucket("gap_cv", [0, 1, 1.5, 2.5, 50])
bucket("burst_share", [0, .2, .5, .8, 1])
bucket("micro_share_30m", [0, .05, .2, .5, 1])
bucket("wash_share", [0, .001, .1, .3, 1])

BASE = ["dev_supply", "nondev_s0", "s2_supply", "s0_buyers", "dev_prev_30d", "dev_prev_grad", "dev_grad_rate",
        "sym_before_other", "mayhem", "n_wal_10s", "net_supply_10s"]
ACT5 = ["org_buyers_5m", "flip_share", "mc_5m"]
FP5 = ["size_cv", "dup_size_share", "gap_cv", "burst_share", "direct_share", "top_prog_share", "n_progs"]
ACT30 = ["n_30m", "wal_30m", "vol_30m", "mc_30m"]
FP30 = ["micro_share_30m"]
for col in BASE + ACT5 + FP5 + ACT30 + FP30:
    x[col] = pd.to_numeric(x[col], errors="coerce").astype(float)

tr, te = x[x.week < 3], x[x.week == 3]
print(f"\n\nобучение недели 0-2: {len(tr):,}, проверка неделя 3: {len(te):,}")


def fit(feats, target, sub_tr=tr, sub_te=te):
    m = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, random_state=0)
    m.fit(sub_tr[feats], sub_tr[target])
    p = m.predict_proba(sub_te[feats])[:, 1]
    y = sub_te[target].values
    top = np.argsort(-p)[: max(1, len(p) // 10)]
    return roc_auc_score(y, p), y[top].mean(), y.mean()


for target, sets in [
    ("trap5", {"база 10с": BASE, "+ активность 5 мин": BASE + ACT5, "+ отпечатки 5 мин": BASE + ACT5 + FP5}),
    ("e5_took", {"база 10с": BASE, "+ активность 5 мин": BASE + ACT5, "+ отпечатки 5 мин": BASE + ACT5 + FP5}),
]:
    print(f"\nцель: {target}")
    for name, fs in sets.items():
        auc, top, base = fit(fs, target)
        print(f"  {name:22s} AUC {auc:.3f} | топ-10%: {top:.3f} при базе {base:.3f}")

t30tr, t30te = tr[tr.l30_spent >= 0.5], te[te.l30_spent >= 0.5]
print("\nцель: trap30 (розница после 30 мин), решение на 30-й минуте")
for name, fs in {"база+5 мин": BASE + ACT5 + FP5, "+ активность 30 мин": BASE + ACT5 + FP5 + ACT30,
                 "+ микросделки 30 мин": BASE + ACT5 + FP5 + ACT30 + FP30}.items():
    auc, top, base = fit(fs, "trap30", t30tr, t30te)
    print(f"  {name:22s} AUC {auc:.3f} | топ-10%: {top:.3f} при базе {base:.3f}")

