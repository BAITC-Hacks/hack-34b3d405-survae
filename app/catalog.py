"""Catalog facts, retrieval and conservative technical comparisons.

The model never supplies prices, stock quantities or catalog attributes.
"""
import asyncio
import copy
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
import truststore
import ssl

ROOT = Path(__file__).resolve().parent.parent
API = "https://ekt.kz/api/products"
PROPERTY_LABELS = {
    "KOLICHESTVO_POLYUSOV": "Количество полюсов",
    "NOMINALNYY_TOK": "Номинальный ток",
    "NOMINALNOE_NAPRYAZHENIE": "Номинальное напряжение",
    "NOMINALNAYA_OTKLYUCHAYUSHCHAYA_SPOSOBNOST": "Отключающая способность",
    "KHARAKTERISTIKA_SRABATYVANIYA": "Характеристика срабатывания",
    "TORGOVAYA_MARKA": "Бренд", "TIP_USTANOVKI": "Тип установки",
    "MOSHCHNOST": "Мощность", "STEPEN_ZASHCHITY": "Степень защиты",
    "TSVETOVAYA_TEMPERATURA": "Цветовая температура", "TSOKOL": "Цоколь",
    "SECHENIE": "Сечение", "KOLICHESTVO_ZHIL": "Количество жил",
    "OBYEM": "Тип устройства",
}
CITY_NAMES = {"астана": ["астана", "нур-султан"], "алматы": ["алматы"],
              "шымкент": ["шымкент"], "караганда": ["караганда"],
              "атырау": ["атырау"], "актау": ["актау"], "тараз": ["тараз"],
              "усть-каменогорск": ["усть-каменогорск"], "талдыкорган": ["талдыкорган"]}


def normalized(value):
    return re.sub(r"[^\wа-яё]+", " ", str(value).lower().replace("ё", "е")).strip()


def number(value, default=None):
    try:
        result = float(str(value).replace(" ", "").replace(",", "."))
        return result if result >= 0 and result < 1e12 else default
    except (TypeError, ValueError):
        return default


def safe_url(value):
    if not isinstance(value, str):
        return None
    return value if urlparse(value).scheme == "https" else None


def clean_html(text):
    import html
    return html.unescape(re.sub(r"<[^>]+>", " ", str(text or ""))).strip()


def normalize_product(raw, mode="live"):
    props = raw.get("properties") or {}
    if not isinstance(props, dict):
        props = {}
    attrs = {label: clean_html(props[key]) for key, label in PROPERTY_LABELS.items()
             if key in props and isinstance(props[key], (str, int, float)) and str(props[key]).strip()}
    attrs.update({str(k): clean_html(v) for k,v in (raw.get("attributes") or {}).items()})
    name = clean_html(raw.get("name", "Без названия"))
    warnings = list(raw.get("warnings") or [])
    named_current = re.search(r"(?<![\w.])([\d.,]+)\s*[аa](?!\w)", name.lower())
    prop_current = re.search(r"[\d.,]+", str(props.get("NOMINALNYY_TOK", "")))
    if named_current and prop_current and number(named_current.group(1)) != number(prop_current.group()):
        warnings.append("Ток в названии и характеристиках различается. Нужна проверка менеджером; автоматический подбор аналога ограничен.")
    certificates = []
    for key, value in props.items():
        if any(s in key.lower() for s in ["cert", "sert", "сертиф"]):
            for link in value if isinstance(value, list) else [value]:
                if safe_url(link):
                    certificates.append({"title": "Сертификат из каталога", "url": link})
    certificates.extend(raw.get("certificates") or [])
    url = safe_url(raw.get("url"))
    parts = urlparse(url or "").path.split("/")
    category = raw.get("category") or (parts[3] if len(parts) > 4 else "Каталог")
    return {
        "id": str(raw["id"]), "name": name, "article": str(raw.get("article") or raw["id"]),
        "supplier_article": str(props.get("ARTIKULPOSTAVSHCHIKA", "")),
        "price": number(raw.get("price")), "quantity": number(raw.get("quantity")),
        "stores": [{"id": str(s.get("id", "")), "name": str(s.get("name", "")),
                    "quantity": number(s.get("quantity"))} for s in (raw.get("stores") or [])],
        "description": clean_html(raw.get("description")), "attributes": attrs,
        "certificates": certificates, "image": safe_url(raw.get("image")), "url": url,
        "category": category, "minimum": number(props.get("KRATNOST_MIN"), 1) or 1,
        "warnings": warnings, "source": "ekt.kz API" if mode == "live" else "Синтетический пример",
        "mode": mode, "updated_at": int(time.time()), "detailed": "quantity" in raw,
    }


def stock_for(product, city):
    aliases = CITY_NAMES.get(city.lower(), [city.lower()])
    stores = [s for s in product.get("stores", []) if any(a in s["name"].lower() for a in aliases)]
    if not stores:
        return None
    if any(s["quantity"] is None for s in stores):
        return None
    return sum(s["quantity"] for s in stores)


def public_product(product, city):
    # Proposals must snapshot nested characteristics, not share mutable catalog data.
    return {**copy.deepcopy(product), "available": stock_for(product, city), "city": city}


def terms(query):
    words = normalized(query).split()
    aliases = {"автомат": "выключатель", "автоматы": "выключатель", "лампочка": "лампа",
               "лампочки": "лампа", "провод": "кабель", "левранд": "legrand", "иек": "iek"}
    stop = {"мне", "нужен", "нужна", "нужно", "нужны", "есть", "ли", "покажи", "найди", "наличие",
            "цена", "сколько", "стоит", "товар", "артикул", "купить", "хочу", "для", "пожалуйста",
            "по", "на", "в", "из", "и", "с", "шт", "штук", "добавь", "корзину", "аналог", "аналоги"}
    return [aliases.get(w, w) for w in words if w not in stop and w not in CITY_NAMES and len(w) > 1]


def search_score(product, query):
    q = normalized(query)
    article = normalized(product["article"])
    supplier = normalized(product.get("supplier_article", ""))
    if q in [article, supplier, product["id"]]:
        return 200
    if article and article in q:
        return 100
    haystack = normalized(" ".join([product["name"], product["article"], product.get("supplier_article", ""),
                                  " ".join(product.get("attributes", {}).values())]))
    score = 0
    for word in terms(query):
        if word in haystack:
            score += 6 if any(c.isdigit() for c in word) else 3
        elif len(word) > 4 and word[:5] in haystack:
            score += 1
    return score


def analogue_reason(original, candidate, city):
    """Reject uncertain electrical replacements, not just semantically similar names."""
    if original["id"] == candidate["id"] or original["category"] != candidate["category"]:
        return None
    if (stock_for(candidate, city) or 0) <= 0 or original["warnings"] or candidate["warnings"]:
        return None
    a, b = original["attributes"], candidate["attributes"]
    category = str(original["category"]).lower()
    if "выключател" in normalized(original["name"]) or "vyklyuchatel" in category:
        required = ["Количество полюсов", "Номинальный ток", "Номинальное напряжение", "Отключающая способность"]
        if "Характеристика срабатывания" in a:
            required.append("Характеристика срабатывания")
    elif "ламп" in normalized(original["name"]) or "lamp" in category:
        required = ["Цоколь", "Мощность", "Цветовая температура"]
    elif "кабел" in normalized(original["name"]) or "kabel" in category:
        required = ["Сечение", "Количество жил", "Материал жилы", "Исполнение"]
    else:
        required = [k for k in a if k not in ["Бренд", "Тип установки"]]
    if len(required) < 2 or not all(a.get(k) and b.get(k) and normalized(a[k]) == normalized(b[k]) for k in required):
        return None
    return "Совпадают: " + ", ".join(f"{k.lower()} — {a[k]}" for k in required) + "."


class Catalog:
    def __init__(self, mode=None):
        self.mode = mode or os.getenv("CATALOG_MODE", "demo")
        self.items = {}
        self.details_time = {}
        self.loading = False
        self.error = None
        self.semaphore = asyncio.Semaphore(5)
        self.client = httpx.AsyncClient(timeout=12, verify=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT),
                                       follow_redirects=False, auth=(os.getenv("EKT_USERNAME", ""), os.getenv("EKT_PASSWORD", "")))
        if self.mode == "demo":
            self.load_demo()

    def load_demo(self):
        raw = json.loads((ROOT / "data/demo_catalog.json").read_text(encoding="utf-8"))
        raw += json.loads((ROOT / "data/demo_kits.json").read_text(encoding="utf-8"))
        self.items = {str(x["id"]): normalize_product(x, "demo") for x in raw}

    async def get_json(self, path="", params=None):
        async with self.semaphore:
            response = await self.client.get(API + path, params=params)
            response.raise_for_status()
            return response.json()

    async def bootstrap(self):
        if self.mode == "demo":
            return
        self.loading = True
        cache = ROOT / ".cache/catalog.json"
        try:
            if cache.exists():
                raw = json.loads(cache.read_text(encoding="utf-8"))
                self.items = {str(x["id"]): normalize_product(x) for x in raw}
            raw_items = []
            pages = min(100, max(1, int(os.getenv("CATALOG_PAGES", "20"))))
            for start in range(1, pages + 1, 4):
                batch = await asyncio.gather(*[self.get_json(params={"page": p}) for p in range(start, min(start+4,pages+1))])
                for page in batch:
                    raw_items.extend(page.get("items", []))
                self.items.update({str(x["id"]): normalize_product(x) for x in raw_items})
                if any(len(page.get("items", [])) < 20 for page in batch):
                    break
            cache.parent.mkdir(exist_ok=True)
            cache.write_text(json.dumps(raw_items, ensure_ascii=False), encoding="utf-8")
            self.error = None
        except Exception:
            self.error = "Каталог временно недоступен. Повторите запрос позже. Остатки не подменяются демонстрационными."
        finally:
            self.loading = False

    async def detail(self, product_id, fresh=False):
        product_id = str(product_id)
        if self.mode == "demo":
            return self.items.get(product_id)
        item = self.items.get(product_id)
        if not product_id.isdigit():
            return None
        if item and item["detailed"] and not fresh and time.time() - self.details_time.get(product_id, 0) < 45:
            return item
        try:
            raw = await self.get_json("/detail", {"id": product_id})
            if not raw.get("id"):
                return None
            item = normalize_product(raw)
            self.items[product_id] = item
            self.details_time[product_id] = time.time()
            return item
        except Exception:
            return None  # No cached stock is used for purchase confirmation.

    async def search(self, query, limit=5):
        scored = sorted(((search_score(p, query), p) for p in self.items.values()), key=lambda x:x[0], reverse=True)
        exact = [p for score,p in scored if score >= 100]
        if exact:
            results = await asyncio.gather(*(self.detail(p["id"]) for p in exact[:limit]))
            return [p for p in results if p]
        chosen = [p for score,p in scored if score > 0][:limit]
        if not chosen and str(query).isdigit():
            p = await self.detail(query)
            return [p] if p else []
        results = await asyncio.gather(*(self.detail(p["id"]) for p in chosen))
        return [p for p in results if p]

    async def alternatives(self, original, city):
        candidates = [p for p in self.items.values() if p["category"] == original["category"] and p["id"] != original["id"]]
        candidates.sort(key=lambda p:search_score(p, original["name"]),reverse=True)
        details = await asyncio.gather(*(self.detail(p["id"]) for p in candidates[:16]))
        result = []
        for candidate in details:
            if candidate and (reason := analogue_reason(original,candidate,city)):
                result.append({**candidate,"reason":reason})
        return sorted(result,key=lambda p:p["price"] if p["price"] is not None else float("inf"))[:3]
