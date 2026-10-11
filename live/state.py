"""state.py — состояние запусков и признаки.

Каждое определение повторяет retro/census.sql и retro/hidden.sql, иначе
модель, обученная на Dune, получит на вход не те числа:

  rs  = слот сделки - слот создания
  rt  = timestamp сделки - timestamp создания (секунды, из самих событий)
  tok = token_amount / 1e6; доля сапплая = tok / 1e9
  «дев» = CreateEvent.creator

Признаки 10 секунд (модель):
  dev_supply      купил дев в слоте 0, доля сапплая
  nondev_s0       купили в слоте 0 все, кроме дева
  s2_supply       все покупки в слотах 0-2
  s0_buyers       сколько разных не-девов купили в слоте 0
  multi_tx        транзакций в слотах 0-5, где торговало >1 кошелька (None, если ни одной)
  max_wal_per_tx  максимум кошельков в такой транзакции (None, если ни одной)
  n_wal_10s       разных кошельков со сделками за 10 с
  net_supply_10s  (купили - продали) за 10 с, доля сапплая
  dev_prev_30d / dev_prev_grad / dev_grad_rate — история дева (None, если истории нет)
  comp_before     запусков с тем же тикером от других девов за 30 мин до
  mayhem          режим mayhem

Признаки 5 минут (второй этап, в модель не входят):
  org_buyers_5m   не-девы, впервые купившие после слота 0 в первые 300 с
  direct_share    доля из них, зашедших прямым вызовом pump (не через роутер)
  top_prog_share  доля самой частой внешней программы
"""
from collections import defaultdict, deque

from .pump_events import PUMP_PROGRAM

SUPPLY = 1e9


class Launch:
    __slots__ = ("mint", "creator", "symbol", "name", "slot", "ts", "mayhem", "seen_at",
                 "trades", "scored", "stage2_done", "wallets", "last_ts", "grad_ts", "peak_mc",
                 "n_tr", "features", "score", "gap", "create_sig", "level", "alerted", "funding")

    def __init__(self, ev, slot, seen_at):
        self.mint = ev["mint"]
        self.creator = ev["creator"]
        self.symbol = ev["symbol"]
        self.name = ev["name"]
        self.slot = slot
        self.ts = ev["timestamp"]
        self.mayhem = 1.0 if ev.get("is_mayhem_mode") else 0.0
        self.seen_at = seen_at
        self.trades = []            # первые 5 минут: (rs, rt, sig, user, is_buy, sol, tok, outer)
        self.scored = False
        self.stage2_done = False
        # для исходов: кошелёк -> [first_rs, first_rt, spent, received]
        self.wallets = {}
        self.last_ts = self.ts
        self.grad_ts = None
        self.peak_mc = 0.0
        self.n_tr = 0
        self.features = None
        self.score = None
        self.gap = False            # запуск пересёк обрыв WebSocket — сделки неполные
        self.create_sig = None      # подпись транзакции создания (для поиска пополнителя дева)
        self.level = None
        self.alerted = False        # красный алерт ушёл в Telegram
        self.funding = None         # итог live/funding.analyse по ранним покупателям

    def add_trade(self, ev, slot, sig, outer):
        rs = slot - self.slot
        rt = ev["timestamp"] - self.ts
        sol = ev["sol_amount"] / 1e9
        tok = ev["token_amount"] / 1e6
        user, is_buy = ev["user"], ev["is_buy"]
        if rt <= 300:
            self.trades.append((rs, rt, sig, user, is_buy, sol, tok, outer))
        w = self.wallets.get(user)
        new = w is None
        if new:
            w = self.wallets[user] = [rs, rt, 0.0, 0.0]
        if is_buy:
            w[2] += sol
        else:
            w[3] += sol
        self.n_tr += 1
        self.last_ts = max(self.last_ts, ev["timestamp"])
        vs, vt = ev["virtual_sol_reserves"], ev["virtual_token_reserves"]
        if vt:
            self.peak_mc = max(self.peak_mc, vs / vt * 1e6)  # капитализация в SOL, как mc в census
        return new                  # новый кошелёк в этом запуске — для счётчика ботов

    # ---------- признаки ----------
    def features_10s(self, devdb, comp_before):
        cr = self.creator
        s0_buy_users, s0_sup, dev_sup, s2_sup, net10 = set(), 0.0, 0.0, 0.0, 0.0
        wal10 = set()
        tx_users = defaultdict(set)
        for rs, rt, sig, user, is_buy, sol, tok, outer in self.trades:
            if is_buy and rs == 0:
                s0_sup += tok
                if user == cr:
                    dev_sup += tok
                else:
                    s0_buy_users.add(user)
            if is_buy and rs <= 2:
                s2_sup += tok
            if rt <= 10:
                net10 += tok if is_buy else -tok
                wal10.add(user)
            if rs <= 5:
                tx_users[sig].add(user)
        multi = [len(u) for u in tx_users.values() if len(u) > 1]
        n30, g30 = devdb.history(cr, self.ts)
        f = {
            "dev_supply": dev_sup / SUPPLY,
            "nondev_s0": max(s0_sup - dev_sup, 0.0) / SUPPLY,
            "s2_supply": s2_sup / SUPPLY,
            "s0_buyers": float(len(s0_buy_users)),
            "multi_tx": float(len(multi)) if multi else None,
            "max_wal_per_tx": float(max(multi)) if multi else None,
            "dev_prev_30d": float(n30) if n30 else None,
            "dev_prev_grad": float(g30) if n30 else None,
            "dev_grad_rate": (g30 / n30) if n30 else None,
            "comp_before": float(comp_before),
            "mayhem": self.mayhem,
            "n_wal_10s": float(len(wal10)),
            "net_supply_10s": net10 / SUPPLY,
        }
        self.features = f
        return f

    def features_5m(self):
        first = {}
        for rs, rt, sig, user, is_buy, sol, tok, outer in self.trades:
            if user == self.creator or not is_buy:
                continue
            if user not in first:
                first[user] = (rs, rt, outer)
        org = [(rs, outer) for rs, rt, outer in first.values() if rs > 0 and rt <= 300]
        if not org:
            return {"org_buyers_5m": 0, "direct_share": None, "top_prog_share": None, "n_progs": 0}
        progs = defaultdict(int)
        for _, outer in org:
            progs[outer] += 1
        return {
            "org_buyers_5m": len(org),
            "direct_share": progs.get(PUMP_PROGRAM, 0) / len(org),
            "top_prog_share": max(progs.values()) / len(org),
            "n_progs": len(progs),
        }

    def outcome(self, is_bot=None):
        """Как census: «поздняя розница» = не дев, не бот, первая сделка после 5-го слота."""
        rest_n = rest_spent = rest_net = l5_spent = l5_net = dev_net = 0.0
        for u, (frs, frt, spent, recv) in self.wallets.items():
            net = recv - spent
            if u == self.creator:
                dev_net += net
                continue
            if is_bot and is_bot(u):
                continue
            if frs > 5:
                rest_n += 1; rest_spent += spent; rest_net += net
            if frt > 300:
                l5_spent += spent; l5_net += net
        return {"rest_n": rest_n, "rest_spent": rest_spent, "rest_net": rest_net,
                "l5_spent": l5_spent, "l5_net": l5_net, "dev_net": dev_net,
                "n_tr": self.n_tr, "n_wal": len(self.wallets), "peak_mc": self.peak_mc,
                "graduated": self.grad_ts is not None,
                "t_grad": (self.grad_ts - self.ts) if self.grad_ts else None,
                "last_ts": self.last_ts}


class TickerWindow:
    """Запуски за последние 30 минут по тикеру — для comp_before."""

    def __init__(self, span=1800):
        self.span = span
        self.q = deque()                       # (ts, sym, creator)
        self.by_sym = defaultdict(list)

    def add_and_count(self, symbol, creator, ts):
        while self.q and self.q[0][0] < ts - self.span:
            t, s, c = self.q.popleft()
            lst = self.by_sym.get(s)
            if lst:
                lst.pop(0)
                if not lst:
                    del self.by_sym[s]
        sym = (symbol or "").strip().lower()
        n = sum(1 for t, c in self.by_sym.get(sym, ()) if c != creator and t < ts)
        self.q.append((ts, sym, creator))
        self.by_sym[sym].append((ts, creator))
        return n
