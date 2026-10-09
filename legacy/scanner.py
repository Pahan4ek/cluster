# -*- coding: utf-8 -*-
import sys, io
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
"""
scanner.py — Solana Gem Dev Watchlist Builder

ЦЕЛЬ: Найти кошельки проверенных девов и сохранить их в Google Sheets
      как ПОСТОЯННЫЙ ВОТЧЛИСТ для снайпинга будущих запусков.

Логика:
  1. Берём токены с большой капой ($500k-$50M) с DexScreener
  2. Для каждого → получаем creator (кошелёк дева)
  3. Анализируем историю дева: сколько успешных токенов сделал
  4. Скорим дева (не токен!) и пишем кошелёк в таблицу
  5. Дубликаты не пишем (один дев = одна строка)

Дальше ты добавляешь эти кошельки в Axiom/BullX/Photon
и снайпишь ВСЁ что они деплоят.

Запуск:
  python scanner.py              -- обычный
  python scanner.py --dry-run   -- без записи в Sheets
  python scanner.py --init      -- создать шапку таблицы
"""

import sys
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import fetcher
import sheets
from config import PROVEN_SCAN_LIMIT, MIN_PROVEN_MC, MAX_PROVEN_MC

DRY_RUN = "--dry-run" in sys.argv
INIT    = "--init"    in sys.argv

# Минимальный скор дева для попадания в таблицу (из 10)
MIN_DEV_SCORE = 5


def score_dev(
    creator:       str,
    proven_tokens: list[dict],
    all_history:   list[dict],
    n_migrated:    int = 0,       # токенов достигших миграции (Raydium)
) -> dict:
    """
    Скор ДЕВА (не токена).

    Критерии:
      +4  Количество успешных токенов (1=+2, 2=+3, 3+=+4)
      +2  Лучший MC ($1M+=+1, $5M+=+2)
      +2  Хороший success rate
      +2  Есть соцсети
    """
    score = 0
    notes = []

    # Кол-во успешных токенов
    n = len(proven_tokens)
    if n >= 3:
        score += 4
        notes.append(f"{n} успешных +4")
    elif n == 2:
        score += 3
        notes.append(f"2 успешных +3")
    elif n == 1:
        score += 2
        notes.append(f"1 успешный +2")

    # Лучший MC
    best_mc = max((t.get("mc", 0) for t in proven_tokens), default=0)
    if best_mc >= 5_000_000:
        score += 2
        notes.append(f"MC ${best_mc/1e6:.1f}M +2")
    elif best_mc >= 1_000_000:
        score += 1
        notes.append(f"MC ${best_mc/1e6:.1f}M +1")

    # Соцсети
    has_socials = any(t.get("twitter") or t.get("telegram") for t in proven_tokens)
    if has_socials:
        score += 2
        notes.append("соцсети +2")
    else:
        notes.append("нет соцсетей")

    # Success rate
    total_tokens = len(all_history)
    migr_rate    = n_migrated / total_tokens if total_tokens > 0 else 0
    if migr_rate >= 0.3 or total_tokens <= 2:
        score += 2
        notes.append(f"migr.rate {migr_rate:.0%} +2")
    else:
        notes.append(f"migr.rate {migr_rate:.0%}")

    grade = "S" if score >= 8 else "A" if score >= 6 else "B" if score >= 5 else "C"

    success_pct = f"{migr_rate:.0%}" if total_tokens > 0 else "N/A"

    return {
        "score":       min(score, 10),
        "grade":       grade,
        "best_mc":     best_mc,
        "n_success":   n,
        "n_total":     total_tokens,
        "n_migrated":  n_migrated,
        "success_pct": success_pct,
        "notes":       " | ".join(notes),
    }


# ─── Загрузка истории от collector.py ────────────────────
DEV_HISTORY_FILE = Path(__file__).parent / "dev_history.json"

def load_dev_history() -> dict:
    """Читаем базу накопленную collector.py."""
    if DEV_HISTORY_FILE.exists():
        try:
            return json.loads(DEV_HISTORY_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def analyze_dev(creator: str, proven_tokens: list[dict], dev_history: dict) -> dict | None:
    """
    Анализ дева.

    ВАЖНО: proven_tokens — это токены дева которые УЖЕ выросли до $500k+.
    Это и есть его подтверждённые миграции. Мы всегда их учитываем
    как минимальную базу, даже если collector ещё не видел этого дева.
    """
    print(f"\n      Дев: {creator[:20]}...")

    # ── 1. Строим базовую историю из proven_tokens ────────
    # Эти токены 100% мигрировали (у них MC > $500k и они торгуются)
    base_history = [
        {
            "mint":     t["mint"],
            "symbol":   t["symbol"],
            "migrated": True,
            "ts":       0,
            "mc":       t.get("mc", 0),
        }
        for t in proven_tokens
    ]
    n_migr_base  = len(proven_tokens)   # минимум подтверждённых миграций
    n_total_base = len(proven_tokens)   # минимум известных запусков

    # ── 2. Дополняем данными из collector.py ─────────────
    history_entry = dev_history.get(creator, {})
    collector_tokens   = history_entry.get("tokens",   [])
    collector_total    = history_entry.get("total",    0)
    collector_migrated = history_entry.get("migrated", 0)

    if collector_tokens:
        # Объединяем: proven + то что видел collector
        # (исключаем дубликаты по mint)
        proven_mints  = {t["mint"] for t in proven_tokens}
        extra_tokens  = [t for t in collector_tokens if t.get("mint") not in proven_mints]
        all_history   = base_history + extra_tokens
        n_total_real  = max(n_total_base, collector_total)
        n_migr_real   = n_migr_base + collector_migrated
        source        = f"collector ({collector_total} токенов) + {len(proven_tokens)} доказанных"
    else:
        # Collector не видел дева — используем только proven как базу
        all_history  = base_history
        n_total_real = n_total_base
        n_migr_real  = n_migr_base
        source       = f"только доказанные ({len(proven_tokens)})"

    # ── 3. Скорим ────────────────────────────────────────
    dev_score = score_dev(creator, proven_tokens, all_history, n_migr_real)
    # Перезаписываем n_total чтобы отражал реальные данные
    dev_score["n_total"]    = n_total_real
    dev_score["n_migrated"] = n_migr_real
    if n_total_real > 0:
        dev_score["success_pct"] = f"{n_migr_real / n_total_real:.0%}"

    grade = dev_score["grade"]
    score = dev_score["score"]

    print(
        f"      Скор: {score}/10 [{grade}] | "
        f"Всего: {n_total_real} | "
        f"Мигрировали: {n_migr_real} | "
        f"% успеха: {dev_score['success_pct']} | "
        f"Лучший MC: ${dev_score['best_mc']/1e6:.2f}M | "
        f"Источник: {source}"
    )

    return dev_score




def run_scan():
    sep = "=" * 64
    print(sep)
    print("  [*] SOLANA GEM DEV WATCHLIST BUILDER")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("  Цель: собрать кошельки доказанных девов для снайпинга")
    if DRY_RUN:
        print("  [!] DRY-RUN — таблица не изменяется")
    print(sep)

    if INIT:
        print("\n[>>] Инициализирую Google Sheets...")
        sheets.init_sheet()
        print("[OK] Готово.")
        return

    # ─── Шаг 1: Токены с большой капой ───────────────────
    print(f"\n[API] Загружаю токены MC ${MIN_PROVEN_MC/1e6:.1f}M - ${MAX_PROVEN_MC/1e6:.0f}M...")
    proven_tokens = fetcher.get_proven_tokens(limit=PROVEN_SCAN_LIMIT)
    print(f"[OK]  Доказанных токенов: {len(proven_tokens)}")

    if not proven_tokens:
        print("[--]  Нет токенов. Попробуй позже.")
        return

    # ─── Загружаем историю от collector.py ───────────────
    dev_history = load_dev_history()
    if dev_history:
        print(f"[OK]  История collector.py: {len(dev_history)} девов в базе")
    else:
        print("[!]   dev_history.json не найден")
        print("      Подсказка: запусти collector.py для сбора реальных данных")
        print("      Пока используем Solscan как резервный источник\n")

    # ─── Шаг 2: Группируем по creator ─────────────────────
    print(f"\n[>>] Определяю кошельки девов...")
    creator_map: dict[str, list[dict]] = {}   # creator → [их proven токены]

    for t in proven_tokens:
        creator = fetcher.get_token_creator_helius(t["mint"])
        if not creator:
            continue
        if creator not in creator_map:
            creator_map[creator] = []
        creator_map[creator].append(t)
        time.sleep(0.2)

    print(f"[OK]  Уникальных девов: {len(creator_map)}")

    # ─── Шаг 3: Скоринг и запись ──────────────────────────
    print(f"\n[>>] Скорю девов (порог: {MIN_DEV_SCORE}/10)...")

    added    = 0
    skipped  = 0

    for i, (creator, tokens) in enumerate(creator_map.items(), 1):
        best = max(tokens, key=lambda x: x.get("mc", 0))
        print(f"\n[{i}/{len(creator_map)}] {best['symbol']} (MC ${best['mc']:,.0f})", end="")

        try:
            dev_score = analyze_dev(creator, tokens, dev_history)
            if not dev_score:
                skipped += 1
                continue

            score = dev_score["score"]

            if score < MIN_DEV_SCORE:
                print(f"      [SKIP] Скор {score} ниже порога {MIN_DEV_SCORE}")
                skipped += 1
                continue

            # Записываем в Sheets
            if not dry_run_check(creator):
                pass  # будет записано ниже

            row_data = build_sheet_row(creator, tokens, dev_score)
            if not DRY_RUN:
                written = sheets.write_dev_wallet(creator, row_data)
                if written:
                    added += 1
                    print(f"      [+] Записан в таблицу!")
                else:
                    print(f"      [=] Уже в таблице")
            else:
                print(f"      [DRY-RUN] Не записываю (скор {score}/10 [{dev_score['grade']}])")
                added += 1

        except Exception as e:
            print(f"\n      [ERR] {e}")
            skipped += 1

        time.sleep(0.3)

    # ─── Итог ─────────────────────────────────────────────
    print("\n" + sep)
    print(f"  [OK] СКАНИРОВАНИЕ ЗАВЕРШЕНО")
    print(f"  Девов проверено:  {len(creator_map)}")
    print(f"  Добавлено в базу: {added}")
    print(f"  Пропущено:        {skipped}")
    print(sep)
    if added > 0 and not DRY_RUN:
        print("\n  Открой Google Sheets — там новые кошельки!")
        print("  Добавь их в Axiom / BullX / Photon как watched wallets")
        print("  и снайпи при следующем запуске.\n")


def dry_run_check(creator: str) -> bool:
    return DRY_RUN


def build_sheet_row(creator: str, tokens: list[dict], dev_score: dict) -> dict:
    """Формируем строку для таблицы."""
    best      = max(tokens, key=lambda x: x.get("mc", 0))
    all_syms  = ", ".join(t["symbol"] for t in tokens)
    best_mc   = dev_score["best_mc"]

    grade_emoji = {"S": "💎 S", "A": "🟢 A", "B": "🟡 B", "C": "🔴 C"}.get(
        dev_score["grade"], dev_score["grade"]
    )

    return {
        "wallet":      creator,
        "grade":       grade_emoji,
        "score":       f"{dev_score['score']}/10",
        "best_token":  best["symbol"],
        "best_mc":     f"${best_mc:,.0f}",
        "all_tokens":  all_syms,
        "n_success":   str(dev_score["n_success"]),
        "n_total":     str(dev_score["n_total"]),
        "n_migrated":  str(dev_score["n_migrated"]),
        "success_pct": dev_score["success_pct"],
        "notes":       dev_score["notes"],
        "solscan":     f"https://solscan.io/account/{creator}",
        "cielo":       f"https://app.cielo.finance/profile/{creator}/pnl/tokens",
        "added":       datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
    }


if __name__ == "__main__":
    run_scan()
