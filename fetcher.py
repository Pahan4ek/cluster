"""
fetcher.py — получение данных из внешних API

Источники:
  DexScreener   — новые токены Solana (pump.fun заблокировал прямые запросы)
  Helius DAS    — creator токена, история транзакций кошелька
  Helius RPC    — топ-холдеры (getTokenLargestAccounts)
  Solscan       — история токенов дева (бэкап)
  pump.fun      — метаданные токена (соцсети, complete-статус)
"""

import time
import requests
from config import HELIUS_API_KEY, REQUEST_DELAY, MIN_PROVEN_MC, MAX_PROVEN_MC

# ─── Endpoints ────────────────────────────────────────────
HELIUS_RPC  = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
HELIUS_API  = f"https://api.helius.xyz/v0"
PUMP_API    = "https://frontend-api.pump.fun"
DEX_API     = "https://api.dexscreener.com/latest/dex"
SOLSCAN_API = "https://public-api.solscan.io"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept":     "application/json",
}


# ─── Базовые HTTP-хелперы ─────────────────────────────────

def _get(url: str, params: dict = None, extra_headers: dict = None) -> dict | list | None:
    """Безопасный GET с retry."""
    hdrs = {**HEADERS, **(extra_headers or {})}
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, headers=hdrs, timeout=12)
            if r.status_code == 200:
                return r.json()
            elif r.status_code == 429:
                wait = 5 * (attempt + 1)
                print(f"    [429] Rate limit ({url[:50]}...) — жду {wait}с")
                time.sleep(wait)
            else:
                return None
        except Exception as e:
            print(f"    [ERR] GET {url[:50]}: {e}")
            time.sleep(1)
    return None


def _rpc(method: str, params: list) -> dict | None:
    """Helius JSON-RPC POST."""
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    for attempt in range(3):
        try:
            r = requests.post(HELIUS_RPC, json=payload, timeout=15)
            if r.status_code == 200:
                return r.json().get("result")
            time.sleep(1 + attempt)
        except Exception as e:
            print(f"    [ERR] RPC {method}: {e}")
            time.sleep(1)
    return None


# ─── СТРАТЕГИЯ: доказанные девы ───────────────────────────

def get_proven_tokens(limit: int = 50) -> list[dict]:
    """
    Получить Solana-токены которые уже выросли до большой капы.
    Источники: token-boosts + поиск трендовых пар по запросам.
    """
    seen_mints = set()
    results    = []

    def _process_pair(pair: dict):
        if len(results) >= limit:
            return
        if not pair or pair.get("chainId") != "solana":
            return
        mint = (pair.get("baseToken") or {}).get("address", "")
        if not mint or mint in seen_mints:
            return
        seen_mints.add(mint)

        mc = 0.0
        try:
            mc = float(pair.get("marketCap") or pair.get("fdv") or 0)
        except Exception:
            pass

        if not (MIN_PROVEN_MC <= mc <= MAX_PROVEN_MC):
            return

        results.append({
            "mint":       mint,
            "symbol":     (pair.get("baseToken") or {}).get("symbol", "???"),
            "mc":         mc,
            "volume_24h": float((pair.get("volume") or {}).get("h24", 0) or 0),
        })

    # ── Источник 1: топ-бусты ────────────────────────────
    for endpoint in [
        "https://api.dexscreener.com/token-boosts/top/v1",
        "https://api.dexscreener.com/token-boosts/latest/v1",
    ]:
        items = _get(endpoint) or []
        time.sleep(REQUEST_DELAY)
        for item in (items if isinstance(items, list) else []):
            if item.get("chainId") != "solana":
                continue
            mint = item.get("tokenAddress", "")
            if not mint or mint in seen_mints:
                continue
            pair = _get_best_pair(mint)
            if pair:
                _process_pair(pair)
            if len(results) >= limit:
                break

    # ── Источник 2: поиск популярных пар на Solana ───────
    search_queries = ["solana meme", "pump.fun", "sol cat", "sol dog", "solana ai"]
    for q in search_queries:
        if len(results) >= limit:
            break
        data = _get(f"{DEX_API}/search/", params={"q": q})
        time.sleep(REQUEST_DELAY)
        if not data:
            continue
        for pair in (data.get("pairs") or []):
            _process_pair(pair)
            if len(results) >= limit:
                break

    return results



def get_dev_new_token(creator: str, max_age_hours: float = 48) -> dict | None:
    """
    Проверить — запустил ли дев новый токен недавно (< max_age_hours).
    Ищем через Solscan последние транзакции кошелька, фильтруем mint-события.
    Возвращает инфо о новом токене или None.
    """
    if not creator:
        return None

    import datetime as dt
    cutoff_ts = (dt.datetime.now(dt.timezone.utc).timestamp() - max_age_hours * 3600) * 1000

    # Получаем последние транзакции дева через Helius
    url    = f"{HELIUS_API}/addresses/{creator}/transactions"
    params = {"api-key": HELIUS_API_KEY, "limit": 50}
    txs    = _get(url, params)
    time.sleep(REQUEST_DELAY)

    if not isinstance(txs, list):
        return None

    # Ищем транзакции типа создания токена (TOKEN_MINT или похожие)
    for tx in txs:
        tx_ts   = tx.get("timestamp", 0) * 1000
        tx_type = tx.get("type", "")

        if tx_ts < cutoff_ts:
            break  # транзакции идут от новых к старым

        # Ищем событие минта нового токена
        if tx_type not in ("TOKEN_MINT", "CREATE", "INIT_MINT", "UNKNOWN"):
            continue

        # Извлекаем mint из tokenTransfers или accountData
        mint = ""
        for tf in tx.get("tokenTransfers", []):
            if tf.get("fromUserAccount") == creator or tf.get("toUserAccount") == creator:
                mint = tf.get("mint", "")
                break

        if not mint:
            # Пробуем из instructions
            for inst in tx.get("instructions", []):
                if inst.get("programId") == "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA":
                    accounts = inst.get("accounts", [])
                    if accounts:
                        mint = accounts[0]
                        break

        if not mint or mint == creator:
            continue

        # Проверяем через DexScreener что это реальный торгуемый токен
        pair = _get_best_pair(mint)
        if not pair:
            continue

        mc = 0.0
        try:
            mc = float(pair.get("marketCap") or pair.get("fdv") or 0)
        except Exception:
            pass

        # Новый токен должен быть с маленькой капой (ещё не вырос)
        if mc > 200_000:
            continue

        info    = _get(f"https://api.dexscreener.com/token-profiles/latest/v1") or []
        socials = {}
        links   = {}
        if isinstance(info, list):
            match = next((t for t in info if t.get("tokenAddress") == mint), None)
            if match:
                i = match.get("info") or {}
                socials = {s.get("type", ""): s.get("url") for s in i.get("socials", [])}
                links   = {l.get("type", ""): l.get("url") for l in i.get("links",   [])}

        pf = _get_pump_token(mint) or {}

        return {
            "mint":              mint,
            "symbol":            pair.get("baseToken", {}).get("symbol", "???"),
            "name":              pair.get("baseToken", {}).get("name", ""),
            "creator":           creator,
            "usd_market_cap":    mc,
            "created_timestamp": tx_ts,
            "complete":          False,
            "twitter":           pf.get("twitter") or socials.get("twitter") or links.get("twitter"),
            "telegram":          pf.get("telegram") or socials.get("telegram") or links.get("telegram"),
        }

    return None



# ─── DexScreener — новые токены ───────────────────────────

def get_new_tokens(limit: int = 100) -> list[dict]:
    """
    Новые токены Solana через DexScreener /token-profiles/latest/v1.
    Результаты нормализованы под формат совместимый с pump.fun полями.
    """
    raw = _get("https://api.dexscreener.com/token-profiles/latest/v1")
    time.sleep(REQUEST_DELAY)

    if not isinstance(raw, list):
        return []

    solana = [t for t in raw if t.get("chainId") == "solana"]
    result = []

    for t in solana[:limit]:
        mint = t.get("tokenAddress", "")
        if not mint:
            continue

        pair = _get_best_pair(mint)
        if not pair:
            continue

        # MC
        mc = 0.0
        try:
            mc = float(pair.get("marketCap") or pair.get("fdv") or 0)
        except Exception:
            pass

        # Timestamp создания пары
        created_ts = None
        try:
            pts = pair.get("pairCreatedAt")
            if pts:
                created_ts = int(pts)   # уже в ms
        except Exception:
            pass

        # Соцсети из DexScreener info
        info    = t.get("info") or {}
        socials = {s.get("type", ""): s.get("url", "") for s in info.get("socials", [])}
        links   = {l.get("type", ""): l.get("url", "") for l in info.get("links",   [])}

        # Данные pump.fun (creator + complete + better socials)
        pf = _get_pump_token(mint)
        creator  = (pf or {}).get("creator", "")
        complete = (pf or {}).get("complete", False)
        tw = (pf or {}).get("twitter") or socials.get("twitter") or links.get("twitter")
        tg = (pf or {}).get("telegram") or socials.get("telegram") or links.get("telegram")

        # Если pump.fun не отдал creator — получим через Helius DAS
        if not creator:
            creator = get_token_creator_helius(mint)

        # Если pump.fun не отдал timestamp — используем DexScreener
        if not created_ts and pf:
            pts2 = pf.get("created_timestamp")
            if pts2:
                created_ts = int(pts2)  # pump.fun тоже в ms

        result.append({
            "mint":              mint,
            "symbol":            pair.get("baseToken", {}).get("symbol", "???"),
            "name":              pair.get("baseToken", {}).get("name", ""),
            "creator":           creator,
            "usd_market_cap":    mc,
            "created_timestamp": created_ts,      # ms
            "complete":          complete,
            "twitter":           tw,
            "telegram":          tg,
        })

    return result


def _get_best_pair(mint: str) -> dict | None:
    """Лучшая (наиболее ликвидная) пара токена с DexScreener."""
    data = _get(f"{DEX_API}/tokens/{mint}")
    time.sleep(REQUEST_DELAY)
    if not data:
        return None
    pairs = data.get("pairs") or []
    sol   = [p for p in pairs if p.get("chainId") == "solana"] or pairs
    if not sol:
        return None
    sol.sort(key=lambda p: float((p.get("liquidity") or {}).get("usd", 0) or 0), reverse=True)
    return sol[0]


# ─── pump.fun — метаданные токена ─────────────────────────

def _get_pump_token(mint: str) -> dict | None:
    """Получить данные токена с pump.fun (creator, complete, socials)."""
    data = _get(f"{PUMP_API}/coins/{mint}")
    time.sleep(REQUEST_DELAY)
    return data  # None если 403


def get_dev_past_tokens_pumpfun(wallet: str, limit: int = 10) -> list[dict]:
    """Прошлые токены дева через pump.fun API."""
    data = _get(f"{PUMP_API}/coins/user-created-coins/{wallet}",
                params={"offset": 0, "limit": limit})
    time.sleep(REQUEST_DELAY)
    return data if isinstance(data, list) else []


# ─── Helius DAS — creator токена ──────────────────────────

def get_token_creator_helius(mint: str) -> str:
    """
    Получить создателя токена: ищем первую транзакцию с mint-адресом
    через getSignaturesForAddress → getTransaction → fee payer = creator.
    """
    # 1. Получаем последние сигнатуры для mint-аккаунта
    sigs_result = _rpc("getSignaturesForAddress", [
        mint,
        {"limit": 5, "commitment": "finalized"}
    ])
    time.sleep(REQUEST_DELAY)

    if not sigs_result or not isinstance(sigs_result, list):
        return ""

    # Берём самую старую (последнюю в списке = первую по времени)
    oldest_sig = sigs_result[-1].get("signature", "")
    if not oldest_sig:
        return ""

    # 2. Получаем транзакцию и берём fee payer (первый accountKey)
    tx_result = _rpc("getTransaction", [
        oldest_sig,
        {"encoding": "json", "commitment": "finalized", "maxSupportedTransactionVersion": 0}
    ])
    time.sleep(REQUEST_DELAY)

    if not tx_result:
        return ""

    try:
        keys = tx_result["transaction"]["message"]["accountKeys"]
        if keys:
            return keys[0]  # fee payer = создатель
    except (KeyError, IndexError, TypeError):
        pass

    return ""


# ─── Solscan — прошлые токены дева ───────────────────────

def get_dev_past_tokens_solscan(wallet: str, limit: int = 10) -> list[dict]:
    """
    Найти прошлые токены дева через Solscan: токены где wallet = mint authority.
    Возвращает список нормализованных dict совместимых с pump.fun форматом.
    """
    # Solscan: транзакции аккаунта типа SPL Token Creation
    url    = f"{SOLSCAN_API}/account/token-accounts"
    params = {"account": wallet, "type": "token", "page": 1, "page_size": limit}
    data   = _get(url, params)
    time.sleep(REQUEST_DELAY)

    if not isinstance(data, dict):
        return []

    tokens = data.get("data") or []
    result = []
    for t in tokens:
        mint = t.get("tokenAddress") or t.get("mint", "")
        if not mint:
            continue
        result.append({
            "mint":           mint,
            "symbol":         t.get("tokenSymbol", "???"),
            "complete":       False,   # не знаем — проверим через DexScreener
            "usd_market_cap": 0,
        })
    return result


def get_dev_past_tokens(wallet: str, limit: int = 10) -> list[dict]:
    """
    Главная функция получения прошлых токенов дева.
    Пробует pump.fun → Solscan как бэкап.
    """
    if not wallet:
        return []

    # 1. Пробуем pump.fun
    tokens = get_dev_past_tokens_pumpfun(wallet, limit)
    if tokens:
        return tokens

    # 2. Бэкап: Solscan
    print("      [i] pump.fun недоступен — пробую Solscan...")
    tokens = get_dev_past_tokens_solscan(wallet, limit)

    # 3. Для каждого токена Solscan проверяем complete через DexScreener
    enriched = []
    for t in tokens[:limit]:
        mint  = t.get("mint", "")
        pair  = _get_best_pair(mint) if mint else None
        if pair:
            mc = 0.0
            try:
                mc = float(pair.get("marketCap") or pair.get("fdv") or 0)
            except Exception:
                pass
            # Если есть ликвидность и пара существует — считаем "дошёл"
            liq = float((pair.get("liquidity") or {}).get("usd", 0) or 0)
            t["complete"]       = liq > 5000    # >$5k ликвидности = дошёл до DEX
            t["usd_market_cap"] = mc
        enriched.append(t)

    return enriched


# ─── Helius RPC — топ-холдеры ────────────────────────────

def get_top_holders(mint: str) -> list[dict]:
    """
    Топ-20 держателей токена через Helius RPC (getTokenLargestAccounts).
    Возвращает [{"address": ..., "amount": ..., "pct": ...}].
    """
    result = _rpc("getTokenLargestAccounts", [mint, {"commitment": "finalized"}])
    time.sleep(REQUEST_DELAY)

    if not result:
        return []

    value = result.get("value", [])
    if not value:
        return []

    total = sum(int(v.get("amount", 0)) for v in value)
    if total == 0:
        return []

    return [
        {
            "address": v.get("address", ""),
            "amount":  int(v.get("amount", 0)),
            "pct":     round(int(v.get("amount", 0)) / total * 100, 2),
        }
        for v in value
    ]


# ─── Helius API — транзакции дева ────────────────────────

def get_dev_transactions(wallet: str, mint: str) -> list[dict]:
    """
    Swap-транзакции кошелька дева через Helius Enhanced Transactions API.
    Фильтруем только транзакции с нашим токеном.
    """
    if not wallet:
        return []

    url    = f"{HELIUS_API}/addresses/{wallet}/transactions"
    params = {"api-key": HELIUS_API_KEY, "limit": 100, "type": "SWAP"}
    data   = _get(url, params)
    time.sleep(REQUEST_DELAY)

    if not isinstance(data, list):
        return []

    return [tx for tx in data if mint in str(tx)]
