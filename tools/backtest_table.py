"""backtest_table.py — таблица отложенной недели из model/live_model.json (Markdown, EN) -> model/backtest.md
Все цифры — модель, обученная на неделях 1-3 (зазор 3 дня), проверка на неделе 4 переписи Dune."""
import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
m = json.loads((ROOT / "model" / "live_model.json").read_text(encoding="utf-8"))
h, roi = m["holdout"], m["roi_by_level"]
n = h["n"]
traps_all = h["base"] * n
n10, n2 = n // 10, n // 50
traps10, traps2 = h["top10"] * n10, h["top2"] * n2
rows = [("🔴 red (top 2%)", roi["red"]["n"], traps2 / n2, roi["red"]),
        ("🟡 yellow (next 8%)", roi["yellow"]["n"], (traps10 - traps2) / (n10 - n2), roi["yellow"]),
        ("🟢 green (rest)", roi["green"]["n"], (traps_all - traps10) / (n - n10), roi["green"])]
out = [f"Held-out week: {n:,} pump.fun launches with ≥ {m['min_retail_sol']:.0f} SOL of retail money "
       f"(trained {m['trained_on']} minus the last week, 3-day gap). AUC {h['auc']:.3f}, trap base rate {h['base']:.1%}.",
       "", "| Level | Launches | Trap rate | vs base | Retail SOL in | Retail ROI | Lost per 1 SOL |",
       "|---|---:|---:|---:|---:|---:|---:|"]
for name, cnt, tr, r in rows:
    out.append(f"| {name} | {cnt:,} | {tr:.1%} | {tr / h['base']:.1f}× | {r['retail_sol']:,.0f} | "
               f"{r['retail_roi']:+.1%} | {-r['retail_roi']:.2f} SOL |")
out += ["", f"Trap = retail (non-dev wallets that entered after slot 5, bots excluded) loses more than half of "
        f"its SOL on a token that never graduates. Thresholds: red ≥ {m['thresholds_holdout_model']['p98']:.3f}, "
        f"yellow ≥ {m['thresholds_holdout_model']['p90']:.3f} (holdout model scale)."]
txt = "\n".join(out)
(ROOT / "model" / "backtest.md").write_text(txt + "\n", encoding="utf-8")
print(txt)
