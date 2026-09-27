"""model.py — инференс модели из model/live_model.json без sklearn.

Формат узла: [feature_idx, threshold, missing_go_to_left, left, right, is_leaf, value]
(так HistGradientBoosting хранит деревья внутри; выгружается retro/train_live.py).
"""
import json
import math
from pathlib import Path


class Model:
    def __init__(self, path):
        obj = json.loads(Path(path).read_text(encoding="utf-8"))
        self.features = obj["features"]
        self.baseline = obj["baseline"]
        self.trees = obj["trees"]
        self.meta = {k: v for k, v in obj.items() if k not in ("trees",)}
        self.red = obj["thresholds"]["p98"]
        self.yellow = obj["thresholds"]["p90"]

    def _tree(self, nodes, x):
        i = 0
        while True:
            f, thr, miss_left, left, right, leaf, val = nodes[i]
            if leaf:
                return val
            v = x[f]
            if v is None or (isinstance(v, float) and math.isnan(v)):
                i = left if miss_left else right
            else:
                i = left if v <= thr else right

    def predict(self, feats: dict) -> float:
        x = [feats.get(f) for f in self.features]
        x = [None if v is None else float(v) for v in x]
        raw = self.baseline + sum(self._tree(t, x) for t in self.trees)
        return 1.0 / (1.0 + math.exp(-raw))

    def level(self, p: float) -> str:
        return "red" if p >= self.red else "yellow" if p >= self.yellow else "green"

    def reasons(self, feats: dict, top=3, min_lift=2.0, min_n=500):
        """Признаки, чья корзина в месяце Dune давала ловушек в min_lift раз больше базы."""
        base = self.meta["base_rate"]
        out = []
        for f, e in self.meta.get("explain", {}).items():
            v = feats.get(f)
            if v is None:
                r, n = e.get("na_rate"), e.get("na_n", 0)
            else:
                edges = e["edges"]
                k = 0
                while k < len(edges) - 2 and v > edges[k + 1]:
                    k += 1
                r, n = e["rate"][k], e["n"][k]
            if r is not None and n >= min_n and r >= base * min_lift:
                out.append((r / base, f, v, r))
        out.sort(key=lambda t: -t[0])
        return [(f, v, r) for _, f, v, r in out[:top]]
