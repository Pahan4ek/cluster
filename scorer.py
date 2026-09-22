"""
scorer.py — подсчёт скоринга дева (0–10 баллов)

Критерии:
  +3  Хотя бы 1 прошлый токен дошёл до DEX/bonding curve
  +2  Дев не продавал в первые 6 часов
  +2  Топ-10 холдеров < 50% supply (с поправкой на возраст)
  +1  Есть Twitter + Telegram
  +1  Текущий MC < $100,000
  +1  Токен < 24 часов
"""

from datetime import datetime, timezone
from config import TOP_HOLDERS_MAX_PCT, CHECK_LAST_N_TOKENS, MAX_MARKET_CAP_USD


def _now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()


def _token_age_hours(token_info: dict) -> float:
    ts = token_info.get("created_timestamp")
    if not ts:
        return 999
    if ts > 1e12:
        ts /= 1000
    return (_now_ts() - ts) / 3600


# ─── Критерии ─────────────────────────────────────────────

def score_past_graduation(past_tokens: list[dict]) -> tuple[int, str]:
    """
    +3 если хотя бы 1 токен дева дошёл до DEX (complete=True или liq > $5k).
    """
    tokens = past_tokens[:CHECK_LAST_N_TOKENS]
    if not tokens:
        return 0, "нет прошлых токенов"

    graduated = [t for t in tokens if t.get("complete") is True]
    if graduated:
        best = max(graduated, key=lambda t: t.get("usd_market_cap") or 0)
        mc   = best.get("usd_market_cap") or 0
        name = best.get("symbol", "???")
        return 3, f"дошёл до DEX: {name} (MC ${mc:,.0f}) ✅"

    return 0, f"из {len(tokens)} токенов ни один не дошёл до DEX ❌"


def score_dev_no_early_dump(
    token_info: dict,
    dev_transactions: list[dict],
) -> tuple[int, str]:
    """
    +2 если дев не делал SELL в первые 6 часов.
    Если данных нет — даём +1 (нейтрально, не штрафуем).
    """
    if not dev_transactions:
        return 1, "нет данных о транзакциях (нейтрально)"

    created_ts = token_info.get("created_timestamp")
    if not created_ts:
        return 1, "нет данных о времени запуска (нейтрально)"

    if created_ts > 1e12:
        created_ts /= 1000

    window_end  = created_ts + 6 * 3600
    dev_wallet  = token_info.get("creator", "")
    mint        = token_info.get("mint", "")

    early_sells = []
    for tx in dev_transactions:
        tx_ts    = tx.get("timestamp", 0)
        transfers = tx.get("tokenTransfers", [])
        sold = any(
            t.get("fromUserAccount") == dev_wallet and t.get("mint") == mint
            for t in transfers
        )
        if sold and created_ts <= tx_ts <= window_end:
            early_sells.append(tx)

    if early_sells:
        return 0, f"дев продал в первые 6ч ({len(early_sells)} транзакций) ❌"

    return 2, "дев не продавал в первые 6 часов ✅"


def score_holder_distribution(
    holders: list[dict],
    token_age_hours: float,
) -> tuple[int, str]:
    """
    +2 если топ-10 держателей < TOP_HOLDERS_MAX_PCT% supply.

    Поправка на возраст: для токенов < 2ч снайперы нормальны,
    поэтому используем смягчённый порог (75%).
    """
    if not holders:
        return 1, "нет данных о холдерах (нейтрально)"

    top10_pct = sum(h["pct"] for h in holders[:10])

    # Очень свежий токен (<2ч) — снайперы неизбежны, используем мягкий порог
    threshold = 75 if token_age_hours < 2 else TOP_HOLDERS_MAX_PCT

    if top10_pct < threshold:
        return 2, f"топ-10 = {top10_pct:.1f}% < {threshold}% ✅"

    return 0, f"топ-10 = {top10_pct:.1f}% >= {threshold}% ❌"


def score_socials(token_info: dict) -> tuple[int, str]:
    """+1 если есть Twitter И Telegram."""
    tw = bool(token_info.get("twitter"))
    tg = bool(token_info.get("telegram"))
    if tw and tg:
        return 1, "Twitter + Telegram ✅"
    if tw:
        return 0, "только Twitter"
    if tg:
        return 0, "только Telegram"
    return 0, "нет соцсетей ❌"


def score_market_cap(token_info: dict) -> tuple[int, str]:
    """+1 если MC < MAX_MARKET_CAP_USD (ранняя стадия)."""
    mc = token_info.get("usd_market_cap") or 0
    if 0 < mc < MAX_MARKET_CAP_USD:
        return 1, f"MC ${mc:,.0f} (ранняя стадия) ✅"
    if mc == 0:
        return 0, "MC неизвестен"
    return 0, f"MC ${mc:,.0f} (уже дорого)"


def score_token_age(token_info: dict) -> tuple[int, str]:
    """+1 если токен < 24 часов."""
    age = _token_age_hours(token_info)
    if age < 24:
        return 1, f"возраст {age:.1f}ч ✅"
    return 0, f"возраст {age:.1f}ч (старый)"


# ─── Главная функция ──────────────────────────────────────

def calculate_score(
    token_info:       dict,
    past_tokens:      list[dict],
    holders:          list[dict],
    dev_transactions: list[dict],
) -> dict:
    """Считает итоговый скор и возвращает детали."""

    age = _token_age_hours(token_info)

    s_grad, d_grad = score_past_graduation(past_tokens)
    s_dump, d_dump = score_dev_no_early_dump(token_info, dev_transactions)
    s_hold, d_hold = score_holder_distribution(holders, age)
    s_soc,  d_soc  = score_socials(token_info)
    s_mc,   d_mc   = score_market_cap(token_info)
    s_age,  d_age  = score_token_age(token_info)

    total = s_grad + s_dump + s_hold + s_soc + s_mc + s_age

    if total >= 8:
        category = "GEM"
    elif total >= 6:
        category = "WATCH"
    else:
        category = "SKIP"

    top10_pct = sum(h["pct"] for h in holders[:10]) if holders else None

    return {
        "total":    total,
        "category": category,
        "breakdown": {
            "graduation": {"score": s_grad, "detail": d_grad},
            "no_dump":    {"score": s_dump, "detail": d_dump},
            "holders":    {"score": s_hold, "detail": d_hold},
            "socials":    {"score": s_soc,  "detail": d_soc},
            "market_cap": {"score": s_mc,   "detail": d_mc},
            "age":        {"score": s_age,  "detail": d_age},
        },
        "top10_pct":       top10_pct,
        "past_graduated":  s_grad == 3,
        "dev_dumped_early": s_dump == 0 and bool(dev_transactions),
    }
