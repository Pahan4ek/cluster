import os
import json
import time
import zipfile
import urllib.request
from DrissionPage import ChromiumPage, ChromiumOptions

from config import CAPSOLVER_API_KEY, PROXY

EXT_PATH = os.path.join(os.path.dirname(__file__), "capsolver_ext")
ZIP_URL = "https://github.com/capsolver/capsolver-browser-extension/releases/download/v1.14.0/capsolver-browser-extension-chrome-v1.14.0.zip"


def setup_capsolver():
    """Скачивает и настраивает расширение CapSolver."""
    if not os.path.exists(EXT_PATH):
        print("[>>] Скачиваю расширение CapSolver...")
        os.makedirs(EXT_PATH)
        zip_path = os.path.join(os.path.dirname(__file__), "capsolver.zip")
        urllib.request.urlretrieve(ZIP_URL, zip_path)
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(EXT_PATH)
        os.remove(zip_path)
        print("[OK] CapSolver скачан.")

    # Вписываем ключ
    config_path = os.path.join(EXT_PATH, "assets", "config.json")
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        
        config["apiKey"] = CAPSOLVER_API_KEY
        config["enabledForCloudflare"] = True
        
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)


def create_browser() -> ChromiumPage:
    """Создает браузер с антидетектом, прокси и CapSolver (опционально)."""
    co = ChromiumOptions()
    
    # Изолированный профиль, чтобы не было конфликтов с основным браузером
    profile_path = os.path.join(os.path.dirname(__file__), "chrome_profile")
    co.set_user_data_path(profile_path)
    co.auto_port()
    
    # Подключаем расширение CapSolver только если введён ключ
    if CAPSOLVER_API_KEY and "СЮДА_ВСТАВЬ" not in CAPSOLVER_API_KEY:
        setup_capsolver()
        co.add_extension(EXT_PATH)
        print("    [Scraper] Автоматическое решение капчи Включено.")
    else:
        print("    [Scraper] API ключ CapSolver не найден. Капчу нужно решать РУКАМИ.")

    # Прокси
    if PROXY:
        # DrissionPage формат: IP:PORT:USER:PASS
        co.set_proxy(PROXY)

    # Настройки а�def get_dev_history_gmgn(wallet: str) -> list[dict]:
    """Скрейпинг истории дева с GMGN.ai"""
    print(f"    [Scraper] Запуск браузера для {wallet}...")
    page = None
    try:
        page = create_browser()
        
        url = f"https://gmgn.ai/defi/quotation/v1/tokens/creator/sol/{wallet}?limit=50"
        print(f"    [Scraper] Загружаю API {url}")
        page.get(url)
        
        # Ждем 60с для CF
        for i in range(60):
            text = page.ele('css:body', timeout=1).text
            if text and "Just a moment" not in text and "Cloudflare" not in text:
                break
            time.sleep(1)
            
        text = page.ele('css:body').text
        print(f"    [Scraper] Ответ от сервера: {text[:200]}")
        
        data = json.loads(text)
        
        tokens = data.get("data", {}).get("tokens", [])
        print(f"    [OK] Спарсили {len(tokens)} токенов дева!")
        
        # Нормализация в формат scanner.py
        normalized = []
        for t in tokens:
            normalized.append({
                "mint": t.get("address", ""),
                "symbol": t.get("symbol", ""),
                "mc": float(t.get("market_cap", 0) or 0),
                "complete": t.get("is_show_alert", False) or float(t.get("market_cap", 0) or 0) > 10000,
                "migrated": float(t.get("market_cap", 0) or 0) > 500000 or (t.get("liquidity") and float(t.get("liquidity")) > 5000)
            })
        return normalized
        
    except Exception as e:
        print(f"    [ERR] Ошибка скрапера: {e}")
        return []
    finally:
        if page:
            page.quit()�енов дева!")
        
        # Нормализация в формат scanner.py
        normalized = []
        for t in tokens:
            normalized.append({
                "mint": t.get("address", ""),
                "symbol": t.get("symbol", ""),
                "mc": float(t.get("market_cap", 0) or 0),
                "complete": t.get("is_show_alert", False) or float(t.get("market_cap", 0) or 0) > 10000,
                "migrated": float(t.get("market_cap", 0) or 0) > 500000 or (t.get("liquidity") and float(t.get("liquidity")) > 5000)
            })
        return normalized
        
    except Exception as e:
        print(f"    [ERR] Ошибка скрапера: {e}")
        return []
    finally:
        if page:
            page.quit()


if __name__ == "__main__":
    # Тест
    print("Тестирование скрапера...")
    history = get_dev_history_gmgn("FYdcmHWmySiLqqDpkHv44LAAoNxxaQB3NWpWBjdt97Vt")
    for t in history[:5]:
        print(f"Mint: {t['mint']}, MC: ${t['mc']}, Migrated: {t['migrated']}")

