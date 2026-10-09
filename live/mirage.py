"""mirage.py — поток транзакций pump через Solami Mirage (Yellowstone geyser поверх WebSocket).

Зачем: в logsSubscribe длинные логи обрезаются ("Log truncated"), и такие транзакции
приходится добирать getTransaction-ом — это секунды задержки и RPC-кредиты. Mirage
отдаёт транзакцию целиком (SubscribeUpdate из yellowstone-grpc-proto): события pump
лежат во внутренних инструкциях emit_cpi и не обрезаются, добор не нужен.

Protobuf разбираем руками, только нужные поля — без protoc и сгенерированного кода,
чтобы не зависеть от версии protobuf. Номера полей — geyser.proto и solana-storage.proto
из github.com/rpcpool/yellowstone-grpc (yellowstone-grpc-proto/proto).

Подписка (сохранённый фильтр на стороне Solami) создаётся один раз: ensure_subscription().
Нужен SOLAMI_API_KEY с правами MirageManage (создать) и MirageStream (читать поток).
Если ключ без MirageManage — создай подписку в дашборде и впиши SOLAMI_MIRAGE_ID в .env.
"""
import os

from live.pump_events import EVENT_IX_TAG, PUMP_PROGRAM, b58encode, decode_event

API = "https://api.solami.dev"
STREAM = "wss://ws.solami.dev/mirage/stream/{id}?api_key={key}"
WS = "wss://ws.solami.dev/ws/sol?api_key={key}"
RPC = "https://rpc.solami.dev/sol?api_key={key}"
LABEL = "clusterpulse-pump"
FILTER = {"account_include": [PUMP_PROGRAM], "vote": False, "failed": False, "commitment": "confirmed"}


# ---------- protobuf wire format ----------
def _varint(b, i):
    r = s = 0
    while True:
        c = b[i]
        i += 1
        r |= (c & 0x7F) << s
        if c < 0x80:
            return r, i
        s += 7


def fields(b):
    """bytes protobuf-сообщения -> (номер поля, тип, значение). Значение: int для varint, bytes иначе."""
    i, n = 0, len(b)
    while i < n:
        key, i = _varint(b, i)
        f, wt = key >> 3, key & 7
        if wt == 0:
            v, i = _varint(b, i)
        elif wt == 2:
            ln, i = _varint(b, i)
            v = b[i:i + ln]
            i += ln
        elif wt == 1:
            v = b[i:i + 8]
            i += 8
        elif wt == 5:
            v = b[i:i + 4]
            i += 4
        else:
            raise ValueError(f"protobuf: тип {wt} не поддерживается")
        yield f, wt, v


def _first(b, num):
    for f, _, v in fields(b):
        if f == num:
            return v
    return None


# ---------- SubscribeUpdate -> события pump ----------
def parse(frame):
    """
    Кадр Mirage (bytes SubscribeUpdate) ->
      ("tx", slot, signature, failed, [(event, outer_program)])  — транзакция
      ("ping", ...) / ("other", ...)                              — служебное
    outer_program — программа инструкции верхнего уровня, как в events_from_logs.
    """
    upd = None
    for f, wt, v in fields(frame):
        if f == 4 and wt == 2:                 # SubscribeUpdate.transaction
            upd = v
            break
        if f in (6, 9):                        # ping / pong
            return ("ping",)
    if upd is None:
        return ("other",)
    info, slot = None, 0
    for f, wt, v in fields(upd):               # SubscribeUpdateTransaction
        if f == 1:
            info = v
        elif f == 2:
            slot = v
    if info is None:
        return ("other",)
    sig, tx, meta = b"", None, None
    for f, wt, v in fields(info):              # SubscribeUpdateTransactionInfo
        if f == 1:
            sig = v
        elif f == 3:
            tx = v
        elif f == 4:
            meta = v
    signature = b58encode(sig) if sig else ""
    if meta is None or tx is None:
        return ("tx", slot, signature, False, [])

    failed = False
    inner = []                                 # (index верхней инструкции, [(prog_idx, data)])
    loaded_w, loaded_r = [], []
    for f, wt, v in fields(meta):              # TransactionStatusMeta
        if f == 1 and wt == 2 and len(v):      # err: TransactionError{bytes err}
            failed = True
        elif f == 5:
            idx, ixs = 0, []
            for g, _, w in fields(v):          # InnerInstructions
                if g == 1:
                    idx = w
                elif g == 2:
                    pidx, data = 0, b""
                    for h, _, x in fields(w):  # InnerInstruction
                        if h == 1:
                            pidx = x
                        elif h == 3:
                            data = x
                    if data[:8] == EVENT_IX_TAG:   # всё остальное нам не нужно
                        ixs.append((pidx, data))
            if ixs:
                inner.append((idx, ixs))
        elif f == 12:
            loaded_w.append(v)
        elif f == 13:
            loaded_r.append(v)
    if failed or not inner:
        return ("tx", slot, signature, failed, [])

    msg = _first(tx, 2)                        # Transaction.message
    keys, top = [], []
    for f, wt, v in fields(msg or b""):        # Message
        if f == 2:
            keys.append(v)
        elif f == 4:
            top.append(_first(v, 1) or 0)      # CompiledInstruction.program_id_index
    keys += loaded_w + loaded_r                # порядок индексов в v0-транзакции
    pump = PUMP_PROGRAM
    names = {}

    def name(i):
        s = names.get(i)
        if s is None and i < len(keys):
            s = names[i] = b58encode(keys[i])
        return s

    out = []
    for idx, ixs in inner:
        outer = name(top[idx]) if idx < len(top) else None
        for pidx, data in ixs:
            if name(pidx) != pump:
                continue
            ev = decode_event(bytes(data[8:]))
            if ev:
                out.append((ev, outer))
    return ("tx", slot, signature, False, out)


# ---------- подписка ----------
def _post(path, key, body):
    import requests
    r = None
    for kw in ({"params": {"api_key": key}}, {"headers": {"Authorization": f"Bearer {key}"}}):
        r = requests.post(f"{API}{path}", json=body, timeout=20, **kw)
        if r.status_code not in (401, 403):
            return r
    return r


def _items(j):
    if isinstance(j, list):
        return j
    if isinstance(j, dict):
        for k in ("data", "subscriptions", "items", "result"):
            if isinstance(j.get(k), list):
                return j[k]
    return []


def ensure_subscription(key):
    """id сохранённой подписки на программу pump: из .env, из списка или создаём новую."""
    sid = os.environ.get("SOLAMI_MIRAGE_ID")
    if sid:
        return sid
    r = _post("/mirage/list", key, {})
    if r is not None and r.ok:
        for s in _items(r.json()):
            if s.get("label") == LABEL:
                if s.get("enabled") is False:
                    _post("/mirage/update", key, {"id": s["id"], "enabled": True})
                return s["id"]
    r = _post("/mirage/create", key, {"label": LABEL, "filter": FILTER})
    if r is not None and r.ok:
        j = r.json()
        s = j if isinstance(j, dict) and "id" in j else (j.get("data") if isinstance(j, dict) else None) or {}
        if s.get("id"):
            if s.get("enabled") is False:
                _post("/mirage/update", key, {"id": s["id"], "enabled": True})
            return s["id"]
    code = r.status_code if r is not None else "?"
    text = (r.text[:300] if r is not None else "").replace(key, "<key>")
    raise RuntimeError(f"Mirage: не удалось создать подписку ({code}: {text}). "
                       f"Создай её в дашборде Solami (фильтр: account_include = [{PUMP_PROGRAM}], "
                       f"commitment confirmed) и впиши SOLAMI_MIRAGE_ID в .env")


def stream_url(key, sid):
    return STREAM.format(id=sid, key=key)
