"""train_live.py — модель для живого скорера.

Учится на переписи Dune (dune_out/census_*.csv[.gz]) только на признаках,
которые живой скорер может посчитать из потока событий pump за первые 10 секунд.
copaid_w5 выкинут: в логах нет подписанта транзакции.

Цель: розница (кошельки, впервые зашедшие после 5-го слота, не дев) на
НЕ вышедшем на AMM токене теряет больше половины вложенного.
Выборка: запуски, куда розница занесла хотя бы 1 SOL (там, где есть деньги;
на проверке это ~60% запусков с розницей и почти все её деньги).
Токены, вышедшие на AMM, в цели считаются «не ловушкой»: фазу AMM мы пока
не видим, это известная дыра.

    python retro/train_live.py            -> model/live_model.json

Проверка: учимся на неделях 1-3, проверяем на 4-й. Финальная модель
обучается на всём месяце.
"""
import glob, json, sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "dune_out"
MODEL_DIR = ROOT / "model"
MODEL_DIR.mkdir(exist_ok=True)

FEATURES = ["dev_supply", "nondev_s0", "s2_supply", "s0_buyers", "multi_tx", "max_wal_per_tx",
            "dev_prev_30d", "dev_prev_grad", "dev_grad_rate", "comp_before", "mayhem",
            "n_wal_10s", "net_supply_10s"]
MIN_RETAIL_SOL = 1.0
# корзины для объяснений в алертах (None = признак не определён)
BINS = {"dev_supply": [0, .001, .03, .1, .3, 1], "nondev_s0": [0, .001, .03, .1, .3, 1],
        "s2_supply": [0, .001, .03, .1, .3, 1], "s0_buyers": [0, .5, 2, 5, 1e3],
        "multi_tx": [0, 1.5, 1e3], "max_wal_per_tx": [0, 2.5, 1e3],
        "dev_prev_30d": [0, 5, 50, 500, 1e8], "dev_prev_grad": [-.5, .5, 2, 10, 1e8],
        "dev_grad_rate": [-.001, .001, .01, .05, 1.001], "comp_before": [-.5, .5, 3, 20, 1e5],
        "mayhem": [-.5, .5, 1.5], "n_wal_10s": [-.5, 2, 5, 10, 1e5], "net_supply_10s": [-1, .03, .1, .3, 1]}
COLS = ["mint", "c_time", "g_time", "n_tr", "peak_mc", "s0_supply", "dev_supply", "s2_supply", "s0_buyers",
        "multi_tx", "max_wal_per_tx", "dev_prev_30d", "dev_prev_grad", "sym_before_other", "mayhem",
        "n_wal_10s", "net_supply_10s", "rest_n", "rest_spent", "rest_net"]


def load():
    files = sorted(glob.glob(str(OUT / "census_2026-*.csv*")))
    if not files:
        sys.exit(f"[FATAL] нет файлов census_* в {OUT}")
    d = pd.concat([pd.read_csv(f, usecols=COLS) for f in files], ignore_index=True).drop_duplicates("mint")
    d = d[d.n_tr.notna() & (d.peak_mc > 0)].copy()                 # были сделки, котировка в SOL
    d["c_time"] = pd.to_datetime(d.c_time.str.replace(" UTC", ""), utc=True)
    z = lambda c: d[c].fillna(0).astype(float)
    d["grad"] = d.g_time.notna()
    d["nondev_s0"] = (z("s0_supply") - z("dev_supply")).clip(lower=0)
    d["dev_grad_rate"] = z("dev_prev_grad") / z("dev_prev_30d").replace(0, np.nan)
    d["comp_before"] = z("sym_before_other")
    d["mayhem"] = d.mayhem.astype(str).str.lower().eq("true").astype(float)
    for c in FEATURES:
        d[c] = pd.to_numeric(d[c], errors="coerce").astype(float)
    # были внешние покупатели с деньгами (иначе ловушки не было и быть не могло)
    d = d[(z("rest_n") > 0) & (z("rest_spent") >= MIN_RETAIL_SOL)].copy()
    roi = d.rest_net / d.rest_spent.replace(0, np.nan)
    d["trap"] = (~d.grad) & (roi < -0.5)
    d["week"] = ((d.c_time - d.c_time.min().normalize()).dt.days // 7).clip(upper=3)
    return d


def fit(X, y):
    m = HistGradientBoostingClassifier(max_iter=250, learning_rate=0.06, max_leaf_nodes=31,
                                       min_samples_leaf=200, l2_regularization=1.0, random_state=0)
    m.fit(X, y)
    return m


def export(m, path, meta):
    """Деревья -> JSON. Инференс потом без sklearn (live/model.py)."""
    trees = []
    for it in m._predictors:
        nodes = it[0].nodes
        trees.append([[int(n["feature_idx"]), float(n["num_threshold"]), int(n["missing_go_to_left"]),
                       int(n["left"]), int(n["right"]), int(n["is_leaf"]), float(n["value"])] for n in nodes])
    bl = np.asarray(m._baseline_prediction).ravel()
    obj = {"features": FEATURES, "baseline": float(bl[0]), "trees": trees, **meta}
    Path(path).write_text(json.dumps(obj, separators=(",", ":")), encoding="utf-8")


def main():
    d = load()
    print(f"запусков с внешней розницей: {len(d):,}  ({d.c_time.min():%d.%m}–{d.c_time.max():%d.%m})")
    print(f"доля ловушек: {d.trap.mean():.3f}")

    te = d[d.week == 3]
    # зазор 3 дня: метка считается по сделкам до +3 дней, иначе хвост обучения видит проверочную неделю
    tr = d[d.c_time < te.c_time.min() - pd.Timedelta(days=3)]
    m = fit(tr[FEATURES], tr.trap)
    p = m.predict_proba(te[FEATURES])[:, 1]
    y = te.trap.values
    auc = roc_auc_score(y, p)
    order = np.argsort(-p)
    top10, top2 = order[: len(p) // 10], order[: len(p) // 50]
    print(f"\nпроверка на неделе 4: AUC {auc:.3f} | база {y.mean():.3f} | "
          f"топ-10%: {y[top10].mean():.3f} | топ-2%: {y[top2].mean():.3f}")
    # пороги по квантилям скора на отложенной неделе
    q = {k: float(np.quantile(p, v)) for k, v in [("p90", .90), ("p98", .98)]}
    prec = {k: float(y[p >= t].mean()) for k, t in q.items()}
    print(f"порог красного (топ-2%): {q['p98']:.3f} -> точность {prec['p98']:.3f}; "
          f"жёлтого (топ-10%): {q['p90']:.3f} -> {prec['p90']:.3f}")

    final = fit(d[FEATURES], d.trap)
    # пороги — квантили ТОЙ модели, что уходит в бой: у m и final разная шкала вероятностей,
    # и пороги m на final давали красный у 3,4% запусков вместо 2%
    pf = final.predict_proba(te[FEATURES])[:, 1]
    q_final = {k: float(np.quantile(pf, v)) for k, v in [("p90", .90), ("p98", .98)]}
    # объяснения: доля ловушек по корзинам каждого признака (плюс корзина «нет значения»)
    expl = {}
    for c, edges in BINS.items():
        v = d[c]
        b = pd.cut(v, edges, include_lowest=True)
        g = d.groupby(b, observed=False).trap.agg(["mean", "size"])
        na = d.loc[v.isna(), "trap"]
        expl[c] = {"edges": edges, "rate": [float(x) if n else None for x, n in zip(g["mean"], g["size"])],
                   "n": [int(n) for n in g["size"]],
                   "na_rate": float(na.mean()) if len(na) else None, "na_n": int(len(na))}
    # ROI денег розницы по уровням скора на отложенной неделе — это цифра для людей
    te = te.copy(); te["p"] = p
    bands = {"green": te.p < q["p90"], "yellow": (te.p >= q["p90"]) & (te.p < q["p98"]), "red": te.p >= q["p98"]}
    roi = {k: {"n": int(m_.sum()), "retail_sol": float(te.loc[m_, "rest_spent"].sum()),
               "retail_roi": float(te.loc[m_, "rest_net"].sum() / te.loc[m_, "rest_spent"].sum())}
           for k, m_ in bands.items()}
    for k, v in roi.items():
        print(f"  {k:6s}: запусков {v['n']:>6,} | розница вложила {v['retail_sol']:>9,.0f} SOL | ROI {v['retail_roi']:+.1%}")
    meta = {"target": "розница на не вышедшем токене теряет >50%", "base_rate": float(d.trap.mean()),
            "holdout": {"auc": float(auc), "top10": float(y[top10].mean()), "top2": float(y[top2].mean()),
                        "base": float(y.mean()), "n": int(len(y))},
            "thresholds": q_final, "thresholds_holdout_model": q, "precision_at": prec, "explain": expl, "roi_by_level": roi,
            "min_retail_sol": MIN_RETAIL_SOL,
            "trained_on": f"{d.c_time.min():%Y-%m-%d}..{d.c_time.max():%Y-%m-%d}", "n_train": int(len(d))}
    path = MODEL_DIR / "live_model.json"
    export(final, path, meta)
    # самопроверка экспорта
    sys.path.insert(0, str(ROOT))
    from live.model import Model
    mm = Model(path)
    s = d.sample(3000, random_state=1)
    ref = final.predict_proba(s[FEATURES])[:, 1]
    mine = np.array([mm.predict({c: s.iloc[i][c] for c in FEATURES}) for i in range(len(s))])
    print(f"\nэкспорт: {path} ({path.stat().st_size/1e6:.1f} МБ), расхождение с sklearn: {np.abs(ref-mine).max():.2e}")


if __name__ == "__main__":
    main()
