# -*- coding: utf-8 -*-
"""
funding_graph.py — откуда кошельки получили свой первый SOL

Идея: снайпер или сабы оператора — это свежие кошельки. У свежего кошелька
первая транзакция и есть пополнение. Кто его пополнил — тот и корень.
Если у 4 из 6 первых покупателей токена корень общий, это один человек.

Обход на 2 шага, с кэшем: каждый адрес запрашивается один раз за всё время.
Скрипт прерываемый — Ctrl+C и повторный запуск продолжат с места остановки.

    python funding_graph.py            # строить граф
    python funding_graph.py --report   # только отчёт по готовому графу
"""
import sys, io, time, sqlite3, argparse
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import requests
from rpc import rpc

DB         = "launches.db"
SYSTEM     = "11111111111111111111111111111111"
PAGE       = 1000     # размер страницы getSignaturesForAddress
MAX_PAGES  = 5        # до 5000 сигнатур листаем; дальше корень искать дорого
DELAY      = 0.12
MAX_DEPTH  = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS wallets (
    address    TEXT PRIMARY KEY,
    kind       TEXT,      -- wallet | program | veteran | dead
    owner      TEXT,
    sig_count  INTEGER,
    checked_ts INTEGER
);
CREATE TABLE IF NOT EXISTS funding (
    child      TEXT PRIMARY KEY,
    parent     TEXT,
    sig        TEXT,
    block_time INTEGER,
    lamports   INTEGER,
    depth      INTEGER
);
CREATE INDEX IF NOT EXISTS idx_funding_parent ON funding(parent);
"""


def classify(addr, conn):
    """Кошелёк живого человека или аккаунт, принадлежащий программе."""
    row = conn.execute("select kind, sig_count from wallets where address=?", (addr,)).fetchone()
    if row:
        return row[0]

    info = rpc("getAccountInfo", [addr, {"encoding": "base64"}])
    owner = None
    if info and info.get("value"):
        owner = info["value"].get("owner")

    if owner and owner != SYSTEM:
        kind = "program"       # PDA, токен-аккаунт, вольт — не человек
    elif owner is None:
        kind = "dead"          # аккаунт закрыт или пуст
    else:
        kind = "wallet"

    conn.execute("insert or replace into wallets (address, kind, owner, checked_ts) values (?,?,?,?)",
                 (addr, kind, owner, int(time.time())))
    conn.commit()
    time.sleep(DELAY)
    return kind


def find_funder(addr, conn):
    """
    Самая старая транзакция адреса = его пополнение.

    Листаем постранично до MAX_PAGES. Если сигнатур больше — это
    "ветеран": корень у него древний и про сегодняшний запуск ничего
    не говорит, а листать десятки тысяч подписей дорого. Такой адрес
    НЕ выбрасывается: он идёт по другому признаку — повторной
    встречаемости в запусках (см. report).
    """
    all_sigs, before, pages = [], None, 0
    while pages < MAX_PAGES:
        params = {"limit": PAGE}
        if before:
            params["before"] = before
        batch = rpc("getSignaturesForAddress", [addr, params])
        time.sleep(DELAY)
        if not batch:
            break
        all_sigs.extend(batch)
        pages += 1
        if len(batch) < PAGE:
            break
        before = batch[-1]["signature"]

    conn.execute("update wallets set sig_count=? where address=?", (len(all_sigs), addr))
    if not all_sigs:
        conn.commit()
        return None
    if pages >= MAX_PAGES and len(all_sigs) >= PAGE * MAX_PAGES:
        conn.execute("update wallets set kind='veteran' where address=?", (addr,))
        conn.commit()
        return None
    conn.commit()

    oldest = all_sigs[-1]
    tx = rpc("getTransaction", [oldest["signature"],
             {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])
    time.sleep(DELAY)
    if not tx:
        return None

    meta = tx.get("meta") or {}
    keys = tx.get("transaction", {}).get("message", {}).get("accountKeys", [])
    names = [k.get("pubkey") if isinstance(k, dict) else k for k in keys]
    pre, post = meta.get("preBalances") or [], meta.get("postBalances") or []
    if not names or len(pre) != len(names):
        return None

    try:
        me = names.index(addr)
    except ValueError:
        return None
    if post[me] - pre[me] <= 0:
        return None

    best, best_delta = None, 0
    for i, nm in enumerate(names):
        if i == me:
            continue
        d = post[i] - pre[i]
        if d < best_delta:
            best, best_delta = nm, d

    if not best:
        return None
    return best, oldest["signature"], tx.get("blockTime"), post[me] - pre[me]


def build(conn, limit=None):
    targets = [r[0] for r in conn.execute(
        "select distinct trader from trades where trader is not null")]
    done = {r[0] for r in conn.execute("select child from funding")}
    seen = {r[0] for r in conn.execute("select address from wallets")}

    frontier = [a for a in targets if a not in done]
    if limit:
        frontier = frontier[:limit]
        print(f"ПРОБНЫЙ ПРОГОН: беру {len(frontier)} кошельков из {len(targets)}")
    print(f"кошельков к обходу: {len(frontier)} (уже обработано: {len(done)})")

    for depth in range(1, MAX_DEPTH + 1):
        print(f"\n--- шаг {depth}, адресов: {len(frontier)} ---")
        nxt = []
        for i, addr in enumerate(frontier, 1):
            kind = classify(addr, conn)
            if kind != "wallet":
                if i % 25 == 0:
                    print(f"  {i}/{len(frontier)}")
                continue

            res = find_funder(addr, conn)
            if res:
                parent, sig, bt, lam = res
                conn.execute("""insert or replace into funding
                                (child,parent,sig,block_time,lamports,depth) values (?,?,?,?,?,?)""",
                             (addr, parent, sig, bt, lam, depth))
                conn.commit()
                if parent not in seen:
                    seen.add(parent); nxt.append(parent)

            if i % 25 == 0:
                print(f"  {i}/{len(frontier)} | новых родителей: {len(nxt)}")

        if limit:
            print("  (пробный прогон — второй шаг пропускаю)")
            break
        frontier = [a for a in nxt if a not in done]
        if not frontier:
            break
    print("\nграф построен")


def report(conn):
    q = lambda s, *a: conn.execute(s, a).fetchall()

    print("=== кошельки ===")
    for k, n in q("select kind, count(*) from wallets group by kind order by 2 desc"):
        print(f"  {k or '?':8s} {n}")

    edges = q("select count(*) from funding")[0][0]
    print(f"\nрёбер финансирования: {edges}")

    print("\n=== узлы, раздающие деньги многим ===")
    hubs = q("""select parent, count(distinct child) c from funding
                group by parent having c>2 order by c desc limit 12""")
    for p, c in hubs:
        print(f"  {p[:20]}… → {c} кошельков")

    print("\n=== ЗАПУСКИ С ОБЩИМ КОРНЕМ У ПОКУПАТЕЛЕЙ ===")
    rows = q("""
        select l.mint, l.symbol, f.parent,
               count(distinct t.trader) cluster,
               (select count(distinct t2.trader) from trades t2
                 where t2.mint=l.mint and t2.trader<>l.creator) total
        from launches l
        join trades t   on t.mint = l.mint and t.trader <> l.creator
        join funding f  on f.child = t.trader
        join wallets w  on w.address = t.trader and w.kind='wallet'
        group by l.mint, f.parent
        having cluster >= 2
        order by cluster desc limit 20""")
    if not rows:
        print("  пока ни одного — граф ещё тонкий, нужно больше обойденных кошельков")
    else:
        print(f"  {'токен':14s} {'кластер':>8s} {'из покупателей':>15s}  общий родитель")
        for mint, sym, parent, cl, tot in rows:
            print(f"  {(sym or '?')[:14]:14s} {cl:>8d} {tot or 0:>15d}  {parent[:18]}…")

    # --- второй признак: кто ходит парами ---
    print("\n=== КОШЕЛЬКИ, ЗАХОДЯЩИЕ В ОДНИ И ТЕ ЖЕ ТОКЕНЫ ВМЕСТЕ ===")
    print("(работает и для ветеранов, которым корень финансирования не нужен)")
    pairs = q("""
        select a.trader, b.trader, count(distinct a.mint) together
        from trades a
        join trades b on b.mint = a.mint and b.trader < a.trader
        join wallets wa on wa.address=a.trader and wa.kind in ('wallet','veteran')
        join wallets wb on wb.address=b.trader and wb.kind in ('wallet','veteran')
        where a.trader not in (select creator from launches)
          and b.trader not in (select creator from launches)
        group by a.trader, b.trader
        having together >= 2
        order by together desc limit 15""")
    if not pairs:
        print("  пар пока нет — нужно больше запусков в базе")
        return
    for x, y, n in pairs:
        print(f"  {x[:16]}… + {y[:16]}…  — вместе в {n} запусках")
    return


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--limit", type=int, default=None,
                    help="обойти только N кошельков — для проверки логики")
    a = ap.parse_args()

    conn = sqlite3.connect(DB)
    conn.executescript(SCHEMA); conn.commit()
    try:
        if not a.report:
            build(conn, a.limit)
        report(conn)
    except KeyboardInterrupt:
        print("\n[STOP] прервано — прогресс сохранён, запусти снова чтобы продолжить")
