# -*- coding: utf-8 -*-
"""
parsing.py — разбор транзакций Solana

Отдельно от сбора и от сети: эту логику дёргают и сборщик, и разметка
исходов, и бэктест. Менять её в одном месте — единственный способ
не разъехаться.
"""


def parse_pumpfun_trade(tx: dict, mint: str) -> dict | None:
    """
    Кто и сколько купил/продал.

    Считаем по дельте токен-балансов (pre/postTokenBalances) — это
    устойчивее разбора инструкций, они у разных пулов разные.

    ВАЖНО: брать владельца с максимальной дельтой нельзя. В каждом свопе
    вторая сторона — бондинг-кривая, и её токен-аккаунт меняется ровно
    на столько же. Половина "покупателей" оказывалась самой кривой.

    Критерий: покупатель ПОДПИСЫВАЕТ транзакцию, а PDA кривой подписать
    не может физически. Поэтому среди владельцев с ненулевой дельтой
    берём того, кто есть среди подписантов.
    """
    meta = tx.get("meta") or {}
    if meta.get("err"):
        return None

    keys  = tx.get("transaction", {}).get("message", {}).get("accountKeys", [])
    names, signers = [], set()
    for k in keys:
        if isinstance(k, dict):
            pk = k.get("pubkey")
            if k.get("signer"):
                signers.add(pk)
        else:
            pk = k
        names.append(pk)

    if not signers:                      # без подписантов судить не о чем
        return None

    pre  = {b["accountIndex"]: b for b in meta.get("preTokenBalances", [])  if b.get("mint") == mint}
    post = {b["accountIndex"]: b for b in meta.get("postTokenBalances", []) if b.get("mint") == mint}
    if not post:
        return None

    cand = []
    for idx, pb in post.items():
        owner = pb.get("owner")
        if not owner:
            continue
        post_amt = float(pb["uiTokenAmount"].get("uiAmount") or 0)
        pre_amt  = float(pre.get(idx, {}).get("uiTokenAmount", {}).get("uiAmount") or 0)
        delta    = post_amt - pre_amt
        if abs(delta) > 1e-9:
            cand.append((owner, delta))

    # только подписанты; кривая и вольты отсеиваются здесь
    signed = [(o, d) for o, d in cand if o in signers]
    if not signed:
        return None
    owner, delta = max(signed, key=lambda x: abs(x[1]))

    balances_pre, balances_post = meta.get("preBalances") or [], meta.get("postBalances") or []
    sol_delta = 0.0
    if names and len(balances_pre) == len(names) == len(balances_post):
        try:
            i = names.index(owner)
            sol_delta = (balances_post[i] - balances_pre[i]) / 1e9
        except ValueError:
            sol_delta = 0.0

    return {
        "mint":         mint,
        "trader":       owner,
        "tx_type":      "buy" if delta > 0 else "sell",
        "token_amount": abs(delta),
        "sol_amount":   abs(sol_delta),
    }
