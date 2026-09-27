# -*- coding: utf-8 -*-
"""
rpc.py — единственная точка общения с Helius RPC

Вынесено отдельно, чтобы скрипты анализа не тянули за собой websockets
и asyncio ради трёх функций. Здесь же живут throttle и батчинг, поэтому
лимиты соблюдаются автоматически, кто бы ни вызывал.
"""
import threading
import time

import requests

from config import HELIUS_API_KEY

HELIUS_RPC   = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
RPC_TIMEOUT  = 25
BATCH_SIZE   = 25     # столько вызовов уходит одним POST
MIN_INTERVAL = 0.20   # минимум секунд между запросами, общий на процесс

_lock = threading.Lock()
_last = [0.0]


def _throttle():
    with _lock:
        wait = MIN_INTERVAL - (time.monotonic() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.monotonic()


def _post(payload):
    """POST с экспоненциальной паузой на 429. Ключ из сообщений вырезаем."""
    for attempt in range(5):
        _throttle()
        try:
            r = requests.post(HELIUS_RPC, json=payload, timeout=RPC_TIMEOUT)
            if r.status_code == 429:
                time.sleep(1.5 * (2 ** attempt))
                continue
            if r.status_code == 401:
                raise KeyError("HELIUS_401")
            r.raise_for_status()
            return r.json()
        except KeyError:
            raise
        except Exception as e:
            if attempt == 4:
                print(f"    [RPC] не удалось: {str(e).split('?api-key=')[0]}")
                return None
            time.sleep(1.5 * (2 ** attempt))
    return None


def rpc(method: str, params: list):
    res = _post({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    return res.get("result") if isinstance(res, dict) else None


def rpc_batch(method: str, params_list: list) -> list:
    """Пачка вызовов одним POST — то, что убирает 429."""
    out = []
    for i in range(0, len(params_list), BATCH_SIZE):
        chunk = params_list[i:i + BATCH_SIZE]
        res = _post([{"jsonrpc": "2.0", "id": j, "method": method, "params": p}
                     for j, p in enumerate(chunk)])
        if not isinstance(res, list):
            out.extend([None] * len(chunk))
            continue
        by_id = {r.get("id"): r.get("result") for r in res if isinstance(r, dict)}
        out.extend(by_id.get(j) for j in range(len(chunk)))
    return out


def check_key() -> bool:
    if not HELIUS_API_KEY:
        print("[FATAL] HELIUS_API_KEY пуст. Проверь .env")
        return False
    try:
        rpc("getHealth", [])
    except KeyError:
        print("[FATAL] Helius вернул 401: ключ недействителен.")
        print("        Заведи новый на helius.dev и впиши в .env -> HELIUS_API_KEY=")
        return False
    except Exception as e:
        print(f"[FATAL] Helius недоступен: {e}")
        return False
    print("[OK] ключ Helius принят")
    return True
