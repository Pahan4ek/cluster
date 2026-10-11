"""funding.py — кто пополнил ранних покупателей: отделяем кошельки дева от живых людей.

Для каждого раннего покупателя берём последнюю входящую SOL-транзакцию (>= 0.01 SOL)
перед его первой покупкой — это «пополнитель». Дальше:
  кластер дева = кошельки, которых пополнил дев, пополнитель дева или другой кошелёк кластера;
  группа       = пополнитель (не биржа и не хаб) общий у 3+ покупателей, но с девом не связан;
  свежий       = у кошелька до покупки не больше 2 транзакций.
Биржи — dune_out/cex_solana.csv.gz; хаб — адрес с 1000+ транзакций за последние сутки.

Нужен RPC: RPC_URL, иначе Solami (SOLAMI_API_KEY), иначе Helius. Запросов на лаунч: ~4-8 на кошелёк.
"""
import csv
import gzip
import os
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from live import mirage

ROOT = Path(__file__).resolve().parent.parent
MIN_FUND_LAMPORTS = 10_000_000          # 0.01 SOL
PRIOR_SIGS = 10                         # сколько транзакций до покупки смотрим
MAX_TX_PER_WALLET = 6                   # из них разбираем не больше
GROUP_MIN = 3


def rpc_url():
    u = os.environ.get("RPC_URL")
    if u:
        return u
    k = os.environ.get("SOLAMI_API_KEY")
    if k:
        return mirage.RPC.format(key=k)
    k = os.environ.get("HELIUS_API_KEY")
    return f"https://mainnet.helius-rpc.com/?api-key={k}" if k else None


class RPC:
    """JSON-RPC с общим ограничением запросов в секунду (потокобезопасно)."""

    def __init__(self, url=None, rps=None):
        import requests
        self.url = url or rpc_url()
        if not self.url:
            raise RuntimeError("нет RPC: задай SOLAMI_API_KEY, HELIUS_API_KEY или RPC_URL")
        self.s = requests.Session()
        self.lock = threading.Lock()
        rps = rps or float(os.environ.get("FUNDING_RPS", 40))
        self.min_dt, self.last = 1.0 / rps, 0.0
        self.calls = self.errors = 0
        self.last_err = None                 # для диагностики: последняя ошибка RPC (без ключа)

    def call(self, method, params, tries=3):
        for i in range(tries):
            with self.lock:
                dt = self.last + self.min_dt - time.time()
                if dt > 0:
                    time.sleep(dt)
                self.last = time.time()
            try:
                r = self.s.post(self.url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                                timeout=20)
                self.calls += 1
                if r.status_code == 429:
                    self.last_err = "HTTP 429"
                    time.sleep(1 + i)
                    continue
                j = r.json()
                if "error" in j:
                    self.errors += 1
                    self.last_err = f"{method}: {str(j['error'])[:200]}"
                    time.sleep(0.3 * (i + 1))
                    continue
                return j.get("result")
            except Exception as e:
                self.errors += 1
                self.last_err = f"{method}: {type(e).__name__}: {str(e).split('api_key=')[0].split('api-key=')[0][:150]}"
                time.sleep(0.5 * (i + 1))
        return None

    def sigs(self, addr, before=None, limit=1000):
        p = {"limit": limit}
        if before:
            p["before"] = before
        return self.call("getSignaturesForAddress", [addr, p]) or []

    def tx(self, sig):
        return self.call("getTransaction", [sig, {"encoding": "jsonParsed", "commitment": "confirmed",
                                                  "maxSupportedTransactionVersion": 0}])


def load_cex():
    p = ROOT / "dune_out" / "cex_solana.csv.gz"
    try:
        with gzip.open(p, "rt", encoding="utf-8") as fh:
            return {r["addr"]: r["cex_name"] for r in csv.DictReader(fh)}
    except OSError:
        return {}


def sol_in(tx, wallet):
    """Входящие SOL на wallet в транзакции (jsonParsed): [(источник, лампорты)]."""
    out = []
    if not tx or (tx.get("meta") or {}).get("err") is not None:
        return out
    ixs = list(tx["transaction"]["message"].get("instructions") or [])
    for g in tx["meta"].get("innerInstructions") or ():
        ixs += g.get("instructions") or []
    for ix in ixs:
        p = ix.get("parsed")
        if ix.get("program") != "system" or not isinstance(p, dict):
            continue
        info, t = p.get("info") or {}, p.get("type")
        if t in ("transfer", "transferWithSeed") and info.get("destination") == wallet:
            out.append((info.get("source"), int(info.get("lamports") or 0)))
        elif t in ("createAccount", "createAccountWithSeed") and info.get("newAccount") == wallet:
            out.append((info.get("source"), int(info.get("lamports") or 0)))
    return out


def funder_of(rpc, wallet, before_sig=None):
    """Последний пополнитель кошелька до транзакции before_sig."""
    sigs = rpc.sigs(wallet, before=before_sig, limit=PRIOR_SIGS)
    info = {"funder": None, "lamports": 0, "fund_ts": None, "n_prior": len(sigs)}
    for s in [s for s in sigs if not s.get("err")][:MAX_TX_PER_WALLET]:        # новые сначала
        ins = [(a, l) for a, l in sol_in(rpc.tx(s["signature"]), wallet)
               if a and a != wallet and l >= MIN_FUND_LAMPORTS]
        if ins:
            a, l = max(ins, key=lambda x: x[1])
            info.update(funder=a, lamports=l, fund_ts=s.get("blockTime"))
            break
    return info


def is_hub(rpc, addr):
    s = rpc.sigs(addr, limit=1000)
    return len(s) >= 1000 and (s[0].get("blockTime") or 0) - (s[-1].get("blockTime") or 0) < 86400


def analyse(rpc, dev, buyers, dev_before_sig=None, cex=None, workers=8):
    """
    dev — кошелёк-создатель; buyers — {кошелёк: (подпись первой покупки, SOL в первые минуты)}.
    -> {"infos", "dev_info", "cluster", "groups", "fresh", "hubs", "share_sol", "n"}
    """
    cex = load_cex() if cex is None else cex
    wallets = [w for w in buyers if w != dev]
    def fo(w):
        return funder_of(rpc, w, dev_before_sig if w == dev else buyers[w][0])
    with ThreadPoolExecutor(workers) as ex:
        res = list(ex.map(fo, wallets + [dev]))
    infos = dict(zip(wallets, res[:-1]))
    dev_info = res[-1]
    funders = Counter(i["funder"] for i in infos.values() if i["funder"])
    hubs = {f for f in funders if f in cex}
    cand = [f for f, n in funders.items() if n >= 2 and f not in hubs and f != dev and f not in buyers]
    if dev_info["funder"] and dev_info["funder"] not in cex:
        cand.append(dev_info["funder"])
    with ThreadPoolExecutor(workers) as ex:
        for f, hub in zip(cand, ex.map(lambda f: is_hub(rpc, f), cand)):
            if hub:
                hubs.add(f)
    if dev_info["funder"] in cex:
        hubs.add(dev_info["funder"])
    seeds = {dev}
    if dev_info["funder"] and dev_info["funder"] not in hubs:
        seeds.add(dev_info["funder"])
    cluster = set()
    changed = True
    while changed:                                   # кластер растёт, пока находятся связи
        changed = False
        for w, i in infos.items():
            if w not in cluster and i["funder"] and i["funder"] not in hubs and (
                    i["funder"] in seeds or i["funder"] in cluster):
                cluster.add(w)
                changed = True
        for w in list(cluster):                      # кошелёк кластера пополнил своего пополнителя
            f = infos[w]["funder"]
            if f in infos and f not in cluster and f not in hubs:
                cluster.add(f)
                changed = True
    # общий пополнитель кластера (не дев): тоже часть кластера
    groups = []
    for f, n in funders.items():
        if f in hubs or f in seeds or n < GROUP_MIN:
            continue
        ws = {w for w, i in infos.items() if i["funder"] == f}
        if ws & cluster:
            cluster |= ws
        else:
            groups.append({"funder": f, "wallets": sorted(ws)})
    total = sum(buyers[w][1] for w in wallets) or 1e-9
    return {"n": len(wallets), "infos": infos, "dev_info": dev_info, "cluster": cluster, "groups": groups,
            "fresh": sum(1 for i in infos.values() if i["n_prior"] <= 2),
            "hubs": sorted(hubs), "share_sol": sum(buyers[w][1] for w in cluster) / total,
            "group_wallets": set().union(*[set(g["wallets"]) for g in groups]) if groups else set()}
