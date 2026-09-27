# -*- coding: utf-8 -*-
"""
scoring.py — оценка запуска: насколько ранние покупатели связаны между собой

Главный принцип: СПЕЦИФИЧНОСТЬ, а не частота.

Кошелёк, который сидит в каждом пятом запуске на пампе, про конкретный
токен не говорит ничего — это фон (снайпер-бот, сервис, инфраструктура).
А два кошелька, встретившиеся всего трижды, но каждый раз вместе — это
почти наверняка один человек.

Поэтому пары считаются через lift:  P(A и B) / (P(A) * P(B)).
Для фоновых адресов lift близок к 1 и вклад в скор нулевой, какими бы
частыми они ни были. Обойти это сменой адреса нельзя: новый адрес
редкий, а значит его совпадения ВЕСОМЕЕ, а не легче.

    python scoring.py               # отчёт по всем запускам
    python scoring.py --mint <MINT> # разбор одного
"""
import sys, io, sqlite3, argparse, math, collections
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DB = "launches.db"
AMBIENT_PCT = 0.05    # присутствует более чем в 5% запусков -> фон
MIN_PAIR     = 2      # пара учитывается от стольких совместных появлений
ALPHA        = 0.01   # уровень значимости ДО поправки на множественные сравнения


def load(conn):
    """Кто в каком запуске участвовал, без создателей и без программных адресов."""
    programs = {r[0] for r in conn.execute("select address from wallets where kind='program'")}
    creators = {r[0] for r in conn.execute("select creator from launches where creator is not null")}

    by_mint = collections.defaultdict(set)
    for mint, trader in conn.execute("""
            select t.mint, t.trader from trades t
            join launches l on l.mint=t.mint
            where l.finalized_ts is not null and t.trader is not null"""):
        if trader in programs or trader == mint:
            continue
        by_mint[mint].add(trader)

    return by_mint, creators, programs


def profile(by_mint):
    total = len(by_mint)
    seen = collections.Counter()
    for wallets in by_mint.values():
        seen.update(wallets)
    ambient = {w for w, n in seen.items() if n / total > AMBIENT_PCT}
    return total, seen, ambient


def poisson_tail(k, lam):
    """P(X >= k) при среднем lam. Вероятность увидеть столько совпадений случайно."""
    if lam <= 0:
        return 0.0
    if k <= 0:
        return 1.0
    acc, term = 0.0, math.exp(-lam)
    for i in range(k):
        if i:
            term *= lam / i
        acc += term
    return max(1.0 - acc, 1e-300)


def pair_stats(by_mint, seen, ambient, total):
    """
    Значимость совместных появлений.

    Считаем не lift, а вероятность такого совпадения при независимости.
    Два кошелька, встретившиеся дважды, дают lift в сотни раз просто
    потому что оба редкие — само по себе это ничего не доказывает.

    И главное: пар мы проверяем сотни тысяч. При таком количестве
    проверок редкие совпадения случаются случайно, поэтому порог
    делится на число проверенных пар (поправка Бонферрони).
    """
    pair = collections.Counter()
    for wallets in by_mint.values():
        core = sorted(w for w in wallets if w not in ambient)
        for i, a in enumerate(core):
            for b in core[i + 1:]:
                pair[(a, b)] += 1

    tested = len(pair)
    if not tested:
        return {}, 0, 0.0
    threshold = ALPHA / tested          # поправка на множественные сравнения

    out = {}
    for (a, b), n in pair.items():
        if n < MIN_PAIR:
            continue
        lam = seen[a] * seen[b] / total
        pv = poisson_tail(n, lam)
        if pv <= threshold:
            out[(a, b)] = (n, pv, lam)
    return out, tested, threshold


def score_launch(mint, by_mint, seen, ambient, total, sig, creator_traded):
    wallets = by_mint.get(mint, set())
    core = sorted(w for w in wallets if w not in ambient)
    reasons, score = [], 0

    if creator_traded:
        score += 20
        reasons.append("создатель сам торгует своим токеном")

    best, linked = None, set()
    for i, a in enumerate(core):
        for b in core[i + 1:]:
            key = (a, b)
            if key in sig:
                n, pv, lam = sig[key]
                linked.update(key)
                if best is None or pv < best[1]:
                    best = (n, pv, lam)

    if best:
        n, pv, lam = best
        # -log10(p): 5 -> «раз на 100 000». Шкалируем в 0..50
        strength = min(50, int((-math.log10(pv) - 2) * 6))
        score += max(strength, 5)
        # хвост Пуассона на экстремальных значениях упирается в допущения модели,
        # а не в реальность — поэтому вывод ограничиваем честным потолком
        if pv < 1e-9:
            chance = "меньше 1 к миллиарду"
        else:
            chance = f"1 к {1/pv:,.0f}".replace(",", " ")
        reasons.append(f"совпадение {n} раз при ожидаемых {lam:.3f} — вероятность случайного {chance}")

    if len(linked) >= 4:
        score += 20
        reasons.append(f"связанная группа из {len(linked)} кошельков")
    elif len(linked) == 3:
        score += 10
        reasons.append("связанная группа из 3 кошельков")

    if core and len(wallets) <= 3:
        score += 10
        reasons.append(f"всего {len(wallets)} уникальных покупателей")

    return min(score, 100), reasons


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mint")
    ap.add_argument("--top", type=int, default=15)
    a = ap.parse_args()

    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    by_mint, creators, programs = load(conn)
    total, seen, ambient = profile(by_mint)
    sig, tested, thr = pair_stats(by_mint, seen, ambient, total)

    traded = {r[0] for r in conn.execute("""
        select l.mint from launches l join trades t
        on t.mint=l.mint and t.trader=l.creator""")}
    syms = dict(conn.execute("select mint, symbol from launches"))

    print(f"запусков в выборке: {total} | кошельков: {len(seen)}")
    print(f"фоновых адресов (>{AMBIENT_PCT:.0%} запусков): {len(ambient)} — исключены из пар")
    print(f"проверено пар: {tested:,}".replace(",", " "))
    print(f"порог значимости после поправки Бонферрони: p < {thr:.2e}")
    print(f"пар, прошедших порог: {len(sig)}\n")

    if ambient:
        print("фоновые (их надо разметить вручную — бот/сервис/инфраструктура):")
        for w in sorted(ambient, key=lambda x: -seen[x])[:6]:
            print(f"  {w[:24]}… — в {seen[w]} запусках ({seen[w]/total:.0%})")
        print()

    if a.mint:
        s, r = score_launch(a.mint, by_mint, seen, ambient, total, sig, a.mint in traded)
        print(f"{syms.get(a.mint,'?')} — {s}/100")
        for x in r: print("  •", x)
        return

    scored = []
    for mint in by_mint:
        s, r = score_launch(mint, by_mint, seen, ambient, total, sig, mint in traded)
        if s: scored.append((s, mint, r))
    scored.sort(reverse=True)

    print(f"=== ТОП-{a.top} ПОДОЗРИТЕЛЬНЫХ ЗАПУСКОВ ===")
    for s, mint, r in scored[:a.top]:
        print(f"\n{s:3d}/100  {(syms.get(mint) or '?')[:16]:16s} {mint}")
        for x in r: print(f"         • {x}")

    print(f"\n--- распределение ---")
    for lo, hi in [(70,100),(50,69),(30,49),(1,29)]:
        n = sum(1 for s,_,_ in scored if lo<=s<=hi)
        print(f"  {lo:3d}-{hi:3d}: {n:4d} запусков ({n/total*100:5.1f}% выборки)")
    print(f"  скор 0  : {total-len(scored):4d} ({(total-len(scored))/total*100:5.1f}%)")


if __name__ == "__main__":
    main()
