"""Признаки финансирования и прогрева на запуск: python retro/fund_feats.py 2026-09-01"""
import pandas as pd, numpy as np, glob
import sys
from pathlib import Path
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "dune_out"
def feats(day):
    fu = pd.read_csv(next(OUT.glob(f"funding_{day}.csv*")))
    cexset = set(pd.read_csv(next(OUT.glob("cex_solana.csv*"))).addr)
    fan = fu.groupby("first_f").u.nunique()
    fanl = fu.groupby("last_f").u.nunique()
    hub = set(fan[fan >= 50].index) | set(fanl[fanl >= 50].index) | cexset
    fu["age_h"] = (fu.bt1 - fu.first_ft) / 3600
    fu["pt"] = fu.prior_trades.fillna(0)
    fu["ff"] = fu.first_f.where(~fu.first_f.isin(hub))
    fu["lf"] = fu.last_f.where(~fu.last_f.isin(hub))
    fu["dev_link"] = (fu.first_f == fu.creator) | (fu.last_f == fu.creator)
    fu["fresh"] = fu.age_h <= 24
    fu["young7"] = fu.age_h <= 168
    fu["human_like"] = fu.pt <= 20
    fu["warmed"] = fu.young7 & fu.pt.between(1, 50)
    fu["fb10"] = (fu.first_ft // 600).where(fu.ff.notna())
    fu["amt"] = fu.first_amt.round(3).where(fu.ff.notna())
    def mx(s):  # размер крупнейшей группы одинаковых непустых значений
        s = s.dropna()
        return int(s.value_counts().iloc[0]) if len(s) else 0
    g = fu.groupby("mint")
    out = pd.DataFrame({
        "f_n": g.size(),
        "f_shared_first": g.ff.agg(mx),
        "f_shared_last": g.lf.agg(mx),
        "f_dev_link": g.dev_link.sum(),
        "f_fresh": g.fresh.mean(),
        "f_young7": g.young7.mean(),
        "f_human_like": g.human_like.mean(),
        "f_warmed": g.warmed.mean(),
        "f_sync10": g.fb10.agg(mx),
        "f_dup_amt": g.amt.agg(mx),
        "f_hub_share": g.apply(lambda s: s.first_f.isin(hub).mean(), include_groups=False),
        "f_nofund": g.apply(lambda s: s.first_ft.isna().mean(), include_groups=False),
        "f_prior_med": g.pt.median(),
    })
    return out.reset_index()
if __name__ == "__main__":
    print(feats(sys.argv[1]).describe().T.round(3).to_string())
