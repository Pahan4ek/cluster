"""pump_events.py — разбор событий программы pump.fun из логов транзакции.

Раскладка полей — по официальному IDL (live/pump_idl.json, репозиторий
pump-fun/pump-public-docs). Anchor пишет событие в лог строкой
"Program data: <base64>", где первые 8 байт — дискриминатор события.
Тот же байтовый формат лежит и в данных внутренней инструкции (emit_cpi),
только с 8-байтовым префиксом EVENT_IX_TAG — это для разбора getTransaction.

Разбираем только поля, которые нужны скореру; хвост события (поля, которые
pump дописывает в новых версиях) игнорируется, поэтому добавление полей в
конец структуры разбор не ломает.
"""
import base64
import struct

PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
SOL_QUOTE = "11111111111111111111111111111111"

DISC_TRADE = bytes([189, 219, 127, 211, 78, 230, 97, 238])
DISC_CREATE = bytes([27, 114, 169, 77, 222, 235, 99, 118])
DISC_COMPLETE = bytes([95, 114, 97, 156, 212, 46, 152, 8])
EVENT_IX_TAG = bytes([0xE4, 0x45, 0xA5, 0x2E, 0x51, 0xCB, 0x9A, 0x1D])

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_B58_IDX = {c: i for i, c in enumerate(_B58)}


def b58encode(b: bytes) -> str:
    n = int.from_bytes(b, "big")
    out = []
    while n:
        n, r = divmod(n, 58)
        out.append(_B58[r])
    pad = len(b) - len(b.lstrip(b"\0"))
    return "1" * pad + "".join(reversed(out))


def b58decode(s: str) -> bytes:
    n = 0
    for c in s:
        n = n * 58 + _B58_IDX[c]
    full = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    pad = len(s) - len(s.lstrip("1"))
    return b"\0" * pad + full


class _R:
    """Последовательное чтение borsh."""
    __slots__ = ("b", "i")

    def __init__(self, b, i=0):
        self.b, self.i = b, i

    def take(self, n):
        if self.i + n > len(self.b):
            raise EOFError
        v = self.b[self.i:self.i + n]
        self.i += n
        return v

    def pk(self):
        return b58encode(self.take(32))

    def u64(self):
        return struct.unpack_from("<Q", self.take(8))[0]

    def i64(self):
        return struct.unpack_from("<q", self.take(8))[0]

    def boolean(self):
        return self.take(1)[0] != 0

    def string(self):
        n = struct.unpack_from("<I", self.take(4))[0]
        return self.take(n).decode("utf-8", "replace")


def _trade(r):
    ev = {"kind": "trade", "mint": r.pk(), "sol_amount": r.u64(), "token_amount": r.u64(),
          "is_buy": r.boolean(), "user": r.pk(), "timestamp": r.i64(),
          "virtual_sol_reserves": r.u64(), "virtual_token_reserves": r.u64()}
    # необязательный хвост: у старых версий событий его может не быть
    try:
        r.u64(); r.u64()                    # real_sol_reserves, real_token_reserves
        r.pk(); r.u64(); r.u64()            # fee_recipient, fee_basis_points, fee
        ev["creator"] = r.pk()
        r.u64()                             # creator_fee_basis_points
        ev["creator_fee"] = r.u64()
        r.boolean(); r.u64(); r.u64(); r.u64(); r.i64()
        ev["ix_name"] = r.string()
        ev["mayhem_mode"] = r.boolean()
    except EOFError:
        pass
    return ev


def _create(r):
    ev = {"kind": "create", "name": r.string(), "symbol": r.string(), "uri": r.string(),
          "mint": r.pk(), "bonding_curve": r.pk(), "user": r.pk(), "creator": r.pk(),
          "timestamp": r.i64()}
    ev["quote_mint"] = SOL_QUOTE
    ev["is_mayhem_mode"] = False
    try:
        r.u64(); r.u64(); r.u64(); r.u64()  # virtual_token, virtual_sol, real_token, total_supply
        r.pk()                              # token_program
        ev["is_mayhem_mode"] = r.boolean()
        r.boolean()                         # is_cashback_enabled
        ev["quote_mint"] = r.pk()
    except EOFError:
        pass
    return ev


def _complete(r):
    return {"kind": "complete", "user": r.pk(), "mint": r.pk(), "bonding_curve": r.pk(),
            "timestamp": r.i64()}


_BY_DISC = {DISC_TRADE: _trade, DISC_CREATE: _create, DISC_COMPLETE: _complete}


def decode_event(raw: bytes):
    """Байты события (дискриминатор + поля) -> dict или None."""
    fn = _BY_DISC.get(raw[:8])
    if not fn:
        return None
    try:
        return fn(_R(raw, 8))
    except (EOFError, KeyError, UnicodeError, struct.error):
        return None


def decode_ix_data(data_b58: str):
    """Данные внутренней инструкции emit_cpi (base58) -> событие или None."""
    try:
        raw = b58decode(data_b58)
    except KeyError:
        return None
    if raw[:8] != EVENT_IX_TAG:
        return None
    return decode_event(raw[8:])


def events_from_logs(logs):
    """
    Логи одной транзакции -> [(event, outer_program)].

    outer_program — программа инструкции верхнего уровня, внутри которой
    выполнился pump: сам pump (прямой вызов: сайт pump.fun или свой скрипт)
    или роутер терминала/агрегатора. Строки "Program data" других программ
    не трогаем: смотрим только когда на вершине стека вызовов pump.
    """
    out, stack = [], []
    for line in logs or ():
        if line.startswith("Program data: "):
            if stack and stack[-1] == PUMP_PROGRAM:
                try:
                    raw = base64.b64decode(line[14:])
                except ValueError:
                    continue
                ev = decode_event(raw)
                if ev:
                    out.append((ev, stack[0]))
            continue
        if not line.startswith("Program "):
            continue
        parts = line.split(" ")
        if len(parts) >= 4 and parts[2] == "invoke":
            # "Program <id> invoke [n]"
            try:
                depth = int(parts[3].strip("[]"))
            except ValueError:
                continue
            del stack[depth - 1:]
            stack.append(parts[1])
        elif len(parts) >= 3 and (parts[2] == "success" or parts[2].startswith("failed")):
            if stack and stack[-1] == parts[1]:
                stack.pop()
    return out


def events_from_tx(tx):
    """
    getTransaction (encoding=jsonParsed) -> [(event, outer_program)].

    Запасной путь для транзакций с обрезанными логами ("Log truncated"):
    те же события лежат в inner instructions (emit_cpi) и не обрезаются.
    outer_program — программа инструкции верхнего уровня, как в events_from_logs.
    """
    if not tx or (tx.get("meta") or {}).get("err") is not None:
        return []
    top = [ix.get("programId") for ix in tx["transaction"]["message"]["instructions"]]
    out = []
    for grp in tx["meta"].get("innerInstructions") or ():
        outer = top[grp["index"]] if grp["index"] < len(top) else None
        for ix in grp.get("instructions") or ():
            if ix.get("programId") == PUMP_PROGRAM and "data" in ix:
                ev = decode_ix_data(ix["data"])
                if ev:
                    out.append((ev, outer))
    return out
