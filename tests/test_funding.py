"""Офлайн-проверка live/funding.py на выдуманном RPC.
    python tests/test_funding.py
Схема: дев D пополнен с F0 (не биржа). D пополнил A, B; A пополнил C; F0 пополнил E (тоже кластер).
G пополнил H, I, J (группа без связи с девом). K пополнен с Binance, L и M — с хаба X.
N — активный трейдер: входящих переводов в последних транзакциях нет.
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from live import funding

BIN = "5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9"      # Binance 1 из cex_solana.csv.gz
FUND = {"D": "F0", "A": "D", "B": "D", "C": "A", "E": "F0", "H": "G", "I": "G", "J": "G",
        "K": BIN, "L": "X", "M": "X"}


def transfer(src, dst, lam):
    return {"program": "system", "parsed": {"type": "transfer", "info": {"source": src, "destination": dst, "lamports": lam}}}


class FakeRPC:
    calls = errors = 0

    def sigs(self, addr, before=None, limit=1000):
        if addr == "X":                                  # хаб: 1000 транзакций за час
            return [{"signature": f"x{i}", "blockTime": 1_000_000 - i} for i in range(1000)]
        if addr in ("F0", "G", "D"):
            if addr == "D" and before == "create":
                return [{"signature": "fund-D", "blockTime": 900}]
            return [{"signature": f"{addr}-{i}", "blockTime": 500 - i} for i in range(5)]
        if addr == "N":
            return [{"signature": f"n{i}", "blockTime": 999 - i} for i in range(10)]
        return [{"signature": f"fund-{addr}", "blockTime": 950}, {"signature": f"old-{addr}", "blockTime": 10}]

    def tx(self, sig):
        if sig.startswith("fund-"):
            w = sig[5:]
            ixs = [transfer(FUND[w], w, 500_000_000), transfer(w, "Z", 1000)]
        elif sig.startswith("n"):
            ixs = [transfer("N", "Y", 200_000_000)]       # только исходящие
        else:
            ixs = []
        return {"meta": {"err": None, "innerInstructions": []}, "transaction": {"message": {"instructions": ixs}}}


buyers = {w: (f"buy-{w}", 1.0) for w in "ABCEHIJKLMN"}
r = funding.analyse(FakeRPC(), "D", buyers, dev_before_sig="create")
assert r["dev_info"]["funder"] == "F0", r["dev_info"]
assert r["cluster"] == set("ABCE"), r["cluster"]
assert [g["funder"] for g in r["groups"]] == ["G"] and r["groups"][0]["wallets"] == ["H", "I", "J"], r["groups"]
assert BIN in r["hubs"] and "X" in r["hubs"], r["hubs"]
assert r["infos"]["N"]["funder"] is None and r["infos"]["K"]["funder"] == BIN
assert abs(r["share_sol"] - 4 / 11) < 1e-9 and r["n"] == 11
assert r["fresh"] == 10, r["fresh"]                    # у всех, кроме N, по 2 транзакции
print(f"кластер дева: {sorted(r['cluster'])}, группа: {r['groups'][0]['wallets']}, хабы: {len(r['hubs'])}")
print("ВСЁ ОК")
