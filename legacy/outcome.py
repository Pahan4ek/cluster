# -*- coding: utf-8 -*-
"""
outcome.py — чем кончился запуск

Скорер помечает запуск подозрительным в первые секунды. Здесь мы
возвращаемся к нему через несколько часов и смотрим, что было дальше:
вышли ли ранние покупатели, слил ли создатель, дожил ли токен вообще.

Без этого файла у нас нет права говорить ни о какой точности.

    python outcome.py                # разметить исходы
    python outcome.py --backtest     # сверить скор с исходом
"""
import sys, io, time, sqlite3, argparse, collections
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# переиспользуем RPC-слой сборщика вместе с его throttle и батчингом
from rpc import rpc, rpc_batch
from parsing import parse_pumpfun_trade

DB          = "launches.db"
MIN_AGE_MIN = 90     # раньше полутора часов судить рано
LATE_TX     = 40     # сколько последних транзакций смотрим
EXIT_RUG    = 0.60   # доля вышедших ранних покупателей для метки rug_like

SCHEMA = """
CREATE TABLE IF NOT EXISTS outcomes (
    mint          TEXT PRIMARY KEY,
    checked_ts    INTEGER,
    age_min       INTEGER,
    early_buyers  INTEGER,
    early_exited  INTEGER,
    creator_sold  INTEGER,
    late_trades   INTEGER,   -- разобранных поздних сделок
    total_tx      INTEGER,   -- всего подписей у минта (потолок 1000)
    last_tx_ts    INTEGER,
    label         TEXT
);
"""


def late_trades(mint):
    sigs = rpc("getSignaturesForAddress", [mint, {"limit": 1000}])
    if not sigs:
        return [], 0
    total = len(sigs)
    newest = [e["signature"] for e in sigs[:LATE_TX] if e.get("signature")]
    cfg = {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}
    txs = rpc_batch("getTransaction", [[s, cfg] for s in newest])
    out = []
    for sig, tx in zip(newest, txs):
        if not tx:
            continue
        p = parse_pumpfun_trade(tx, mint)
        if p:
            p["ts"] = (tx.get("blockTime") or 0) * 1000
            out.append(p)
    return out, total


DEAD_SILENCE_MIN = 30   # столько тишины — токен кончился


def label_of(early, exited, creator_sold, total_tx, last_tx_ts, now):
    """
    Исход запуска.

    Исходно я мерил "вышли ли ранние покупатели". Данные это опровергли:
    медианная доля вышедших — ноль. На pump.fun, когда токен умирает,
    продавать уже некому, и розница просто застревает.

    Выходит создатель. Поэтому раг определяется не бегством покупателей,
    а тем, что создатель слил, пока остальные остались в бумаге.
    """
    silent_min = (now - last_tx_ts) / 60000 if last_tx_ts else 10 ** 9
    dead = silent_min > DEAD_SILENCE_MIN

    if total_tx <= 15:
        return "stillborn"            # жизни не было вовсе
    if not dead:
        return "active"               # ещё торгуется
    if creator_sold:
        return "creator_dumped"       # создатель вышел, токен мёртв
    if early and exited / early >= 0.5:
        return "insiders_out"         # создатель держит, но ранние успели выйти
    return "dead"                     # просто умер, никто не нажился


def run():
    conn = sqlite3.connect(DB, timeout=30)
    conn.execute("pragma busy_timeout=30000")

    # схема менялась — если таблица старая, пересоздаём (данных там всё равно нет)
    cols = {r[1] for r in conn.execute("pragma table_info(outcomes)")}
    if cols and "total_tx" not in cols:
        conn.execute("drop table outcomes")
        print("[миграция] старая таблица outcomes удалена")
    conn.executescript(SCHEMA); conn.commit()
    now = int(time.time() * 1000)

    todo = conn.execute("""
        select l.mint, l.symbol, l.creator, l.detected_ts
        from launches l
        where l.finalized_ts is not null
          and l.rpc_trades > 0                      -- без сделок судить не о чем
          and l.mint not in (select mint from outcomes)
          and (? - l.detected_ts) > ?
        order by l.detected_ts""", (now, MIN_AGE_MIN * 60000)).fetchall()

    print(f"запусков к разметке: {len(todo)} (старше {MIN_AGE_MIN} мин)")
    if not todo:
        print("пока нечего — подожди, пока токены дозреют")
        return

    for i, (mint, sym, creator, det) in enumerate(todo, 1):
        early = {r[0] for r in conn.execute(
            "select distinct trader from trades where mint=? and tx_type='buy' and trader<>?",
            (mint, creator or ""))}

        late, total_tx = late_trades(mint)
        sellers = {t["trader"] for t in late if t.get("tx_type") == "sell"}
        exited  = len(early & sellers)
        creator_sold = 1 if creator in sellers else 0
        last_ts = max((t["ts"] for t in late), default=0)
        age_min = int((now - det) / 60000)

        lab = label_of(len(early), exited, creator_sold, total_tx, last_ts, now)
        conn.execute("""insert or replace into outcomes
            (mint,checked_ts,age_min,early_buyers,early_exited,creator_sold,late_trades,total_tx,last_tx_ts,label)
            values (?,?,?,?,?,?,?,?,?,?)""",
            (mint, now, age_min, len(early), exited, creator_sold, len(late), total_tx, last_ts, lab))
        conn.commit()

        if i % 10 == 0 or i == len(todo):
            print(f"  {i}/{len(todo)} | {(sym or '?')[:12]:12s} {lab}")

    print("\nготово")
    for lab, n in conn.execute("select label, count(*) from outcomes group by label order by 2 desc"):
        print(f"  {lab:12s} {n}")


def backtest():
    """Сверка: что скорер пометил и чем это кончилось."""
    import scoring
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=30)

    by_mint, creators, programs = scoring.load(conn)
    total, seen, ambient = scoring.profile(by_mint)
    sig, tested, thr = scoring.pair_stats(by_mint, seen, ambient, total)
    traded = {r[0] for r in conn.execute(
        "select l.mint from launches l join trades t on t.mint=l.mint and t.trader=l.creator")}

    out = dict(conn.execute("select mint, label from outcomes"))
    if not out:
        print("исходов ещё нет — сначала python outcome.py")
        return

    rows = []
    for mint, lab in out.items():
        if mint not in by_mint:
            continue
        s, _ = scoring.score_launch(mint, by_mint, seen, ambient, total, sig, mint in traded)
        rows.append((s, lab))

    print(f"размечено запусков: {len(rows)}\n")
    buckets = [(70, 100, "70-100 (помечен)"), (30, 69, "30-69 (серая зона)"), (0, 29, "0-29 (чистый)")]
    labels = sorted({l for _, l in rows})

    hdr = f"{'скор':20s}" + "".join(f"{l:>12s}" for l in labels) + f"{'всего':>8s}"
    print(hdr); print("-" * len(hdr))
    for lo, hi, name in buckets:
        sel = [l for s, l in rows if lo <= s <= hi]
        cnt = collections.Counter(sel)
        line = f"{name:20s}" + "".join(f"{cnt.get(l,0):>12d}" for l in labels) + f"{len(sel):>8d}"
        print(line)

    print()
    for lo, hi, name in buckets:
        sel = [l for s, l in rows if lo <= s <= hi]
        if not sel:
            continue
        bad = sum(1 for l in sel if l in ("creator_dumped", "insiders_out"))
        print(f"  {name}: плохой исход у {bad}/{len(sel)} = {bad/len(sel)*100:.0f}%")


def relabel():
    """Пересчитать метки по уже сохранённым полям. Ни одного запроса к сети."""
    conn = sqlite3.connect(DB, timeout=30)
    now = int(time.time() * 1000)
    rows = conn.execute("""select mint, early_buyers, early_exited, creator_sold,
                                  total_tx, last_tx_ts from outcomes""").fetchall()
    changed = 0
    for mint, eb, ex, cs, tt, lt in rows:
        lab = label_of(eb or 0, ex or 0, cs or 0, tt or 0, lt or 0, now)
        cur = conn.execute("select label from outcomes where mint=?", (mint,)).fetchone()[0]
        if lab != cur:
            conn.execute("update outcomes set label=? where mint=?", (lab, mint))
            changed += 1
    conn.commit()
    print(f"переразмечено: {changed} из {len(rows)}")
    for l, n in conn.execute("select label, count(*) from outcomes group by label order by 2 desc"):
        print(f"  {l:16s} {n}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--backtest", action="store_true")
    ap.add_argument("--relabel", action="store_true",
                    help="пересчитать метки по собранным данным, без запросов")
    a = ap.parse_args()
    try:
        if a.backtest:
            backtest()
        elif a.relabel:
            relabel()
        else:
            run()
    except KeyboardInterrupt:
        print("\n[STOP] прервано, размеченное сохранено")
