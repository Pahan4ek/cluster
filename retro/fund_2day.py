"""python retro/fund_2day.py — финансирование/прогрев: хвосты и проверка «учусь на одном дне, проверяю на другом».
08-26 выгружен частично (150k из 194,6k строк): кончились кредиты Dune на выгрузку."""
import pandas as pd, numpy as np, warnings; warnings.filterwarnings("ignore")
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from fund_feats import feats, OUT
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score
pd.set_option('display.width',220)
def build(day):
    c=pd.read_csv(next(OUT.glob(f'census_{day}.csv*'))); h=pd.read_csv(next(OUT.glob(f'hidden_{day}.csv*'))); l=pd.read_csv(next(OUT.glob(f'late_{day}.csv*')))
    d=c.merge(h,on='mint',how='left').merge(l,on='mint',how='left').merge(feats(day),on='mint',how='inner')
    z=lambda s:d[s].fillna(0)
    d['grad']=d.g_time.notna(); d['nondev_s0']=(z('s0_supply')-z('dev_supply')).clip(lower=0)
    d['l5_roi']=d.l5_net/d.l5_spent.replace(0,np.nan)
    d=d[z('l5_spent')>=0.5].copy()
    d['trap5']=(~d.grad)&(d.l5_roi<-0.5); d['e5_took']=(d.e5_net>0.5)&(d.l5_net<-0.5)
    d['day']=day; return d
D={k:build(k) for k in ['2026-09-01','2026-08-26']}
for k,d in D.items(): print(k, len(d), 'trap5',round(d.trap5.mean(),3),'took',round(d.e5_took.mean(),3),'grad',round(d.grad.mean(),3))
tails=[('f_human_like',[0,.2,.5,1.01]),('f_dup_amt',[-.5,4.5,99]),('f_sync10',[-.5,4.5,99]),('f_shared_first',[-.5,4.5,99]),('f_warmed',[0,.25,1.01])]
print('\nхвосты: доля trap5 (поздняя розница теряет >50%) / n')
for col,b in tails:
    row=[]
    for k,d in D.items():
        g=d.groupby(pd.cut(d[col],b,include_lowest=True,right=False),observed=True).trap5.agg(['mean','size'])
        row.append(' | '.join(f"{str(i)}: {m:.3f} (n={n})" for i,(m,n) in g.iterrows()))
    print(f"  {col:15s} 09-01: {row[0]}\n  {'':15s} 08-26: {row[1]}")
BASE=['dev_supply','nondev_s0','s2_supply','s0_buyers','dev_prev_30d','dev_prev_grad','sym_before_other','mayhem','n_wal_10s','net_supply_10s','org_buyers_5m','mc_5m','flippers_5m','size_cv','dup_size_share','gap_cv','burst_share','direct_share','top_prog_share','n_progs']
FUND=['f_shared_first','f_shared_last','f_dev_link','f_fresh','f_young7','f_human_like','f_warmed','f_sync10','f_dup_amt','f_hub_share','f_nofund','f_prior_med']
print('\nобучение на одном дне, проверка на другом')
for a,b in [('2026-08-26','2026-09-01'),('2026-09-01','2026-08-26')]:
    tr,te=D[a],D[b]
    for t in ['trap5','e5_took']:
        res=[]
        for fs in [BASE,BASE+FUND]:
            m=HistGradientBoostingClassifier(max_iter=200,learning_rate=0.05,random_state=0,min_samples_leaf=40)
            m.fit(tr[fs].astype(float),tr[t]); p=m.predict_proba(te[fs].astype(float))[:,1]; y=te[t].values
            top=np.argsort(-p)[:len(p)//10]; res.append((roc_auc_score(y,p),y[top].mean(),y.mean()))
        print(f"  {a[5:]}->{b[5:]} {t:8s} база AUC {res[0][0]:.3f} топ10 {res[0][1]:.3f} | +фандинг AUC {res[1][0]:.3f} топ10 {res[1][1]:.3f} | базовая ставка {res[0][2]:.3f}")
