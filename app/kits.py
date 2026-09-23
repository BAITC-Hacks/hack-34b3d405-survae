"""Guided, bounded kit builder. Facts and compatibility come from catalog fields.

The demo has one fully specified modular lighting system. Live mode never uses
demo SKUs: missing system/connector/included-parts data yields an unresolved kit.
"""
import re
import os

from app.ai import extract_kit_slots
from app.catalog import public_product, stock_for, number
from app.commerce import prepare, CommerceError


def local_slots(message, current):
    lower = message.lower().replace("ё", "е")
    result = {}
    length = re.search(r"(?<![\d.,])(\d+(?:[.,]\d+)?)\s*(?:метр|м\b)", lower)
    if not length and current.get("length_m") is None:
        length = re.fullmatch(r"\s*(\d+(?:[.,]\d+)?)\s*", lower)
    if length:
        result["length_m"] = float(length[1].replace(",", "."))
    if re.search(r"(?:нет|без)\s+(?:готовой\s+)?розет|розетк\w*\s+нет|неисправ|сломан", lower):
        result["outlet"] = False
    elif re.search(r"розетк", lower) and re.search(r"есть|готов|рядом", lower):
        result["outlet"] = True
    elif current.get("length_m") is not None and current.get("outlet") is None:
        if lower.strip() in {"да", "есть"}: result["outlet"] = True
        elif lower.strip() == "нет": result["outlet"] = False
    if "тепл" in lower or "3000" in lower:
        result["color"] = "warm"
    elif "нейтрал" in lower or "4000" in lower:
        result["color"] = "neutral"
    if re.search(r"брызг|мокр|влажн|на улице|над мойкой|не сух", lower):
        result["dry"] = False
    elif re.search(r"сух|вдали от воды|без воды", lower):
        result["dry"] = True
    elif current.get("outlet") is True and current.get("dry") is None:
        if lower.strip() == "да": result["dry"] = True
        elif lower.strip() == "нет": result["dry"] = False
    return result


def question(state):
    if state.get("length_m") is None:
        return "Какую длину нужно осветить? Укажите точное число метров, например 2 м.", ["1 м", "2 м", "3 м", "Не знаю"]
    if not 1 <= state["length_m"] <= 4 or not float(state["length_m"]).is_integer():
        return "Этот проверяемый сценарий рассчитан на модули по 1 м и участок от 1 до 4 м. Уточните длину; для другого размера нужен отдельный подбор.", ["1 м", "2 м", "3 м", "4 м"]
    if state.get("outlet") is None:
        return "Рядом есть готовая исправная розетка для подключения блока питания?", ["Есть розетка рядом", "Нет розетки", "Не знаю"]
    if not state["outlet"]:
        return "Сначала нужно согласовать питание с электриком. Без готовой розетки этот комплект нельзя считать полным. После решения вопроса напишите «есть розетка рядом».", []
    if state.get("dry") is None:
        return "Место установки сухое, вдали от воды и брызг?", ["Сухое место, вдали от воды", "Есть брызги", "Не знаю"]
    if not state["dry"]:
        return "Для влажного места требуется отдельный подбор защиты. Этот сценарий проверен только для сухого участка. Уточните условия с мастером.", []
    if state.get("color") is None:
        return "Какой свет предпочитаете: тёплый 3000 К или нейтральный 4000 К?", ["Тёплый свет", "Нейтральный свет"]
    return None


async def build_kit(catalog, session, state):
    temperature = "3000" if state["color"] == "warm" else "4000"
    qty = int(state["length_m"])
    if catalog.mode == "demo":
        modules = [p for p in catalog.items.values() if p["category"] == "lighting_module"]
        supplies = [p for p in catalog.items.values() if p["category"] == "lighting_power"]
    else:
        # A live catalog needs manufacturer compatibility facts. Search only;
        # never infer connectors or substitute synthetic products in live mode.
        modules = await catalog.search(f"светильник подсветка 24 {temperature}", 8)
        supplies = await catalog.search("блок питания 24", 8)
    chosen = None
    for module in modules:
        a = module["attributes"]
        if module["warnings"] or module["price"] is None or (stock_for(module,session["city"]) or 0) < qty:
            continue
        if not (a.get("Температура") == temperature and number(a.get("Длина")) == 1
                and number(a.get("Напряжение")) == 24 and number(a.get("Мощность"))
                and a.get("Разъём") and a.get("Система")
                and all(a.get(k) == "Да" for k in ["Крепёж в комплекте", "Соединительный кабель в комплекте",
                                                 "Встроенный выключатель", "Сухое помещение"])):
            continue
        required_watts = number(a["Мощность"]) * qty * 1.25
        for supply in supplies:
            b = supply["attributes"]
            if (not supply["warnings"] and supply["price"] is not None
                    and (stock_for(supply,session["city"]) or 0) >= 1
                    and number(b.get("Напряжение")) == 24
                    and (number(b.get("Мощность")) or 0) >= required_watts
                    and b.get("Разъём") == a["Разъём"] and b.get("Система") == a["Система"]
                    and b.get("Вилка и кабель в комплекте") == "Да"):
                chosen = (module, supply, required_watts)
                break
        if chosen: break
    kit = {"scenario":"kitchen_lighting", "answers":dict(state), "complete":False,
           "items":[], "checks":[], "unresolved":[], "mode":catalog.mode,
           "notice":"Подбор относится к указанным условиям и комплектности производителя, не является проектом электромонтажа."}
    if not chosen:
        kit["unresolved"] = ["В выборке нет доступной пары с подтверждёнными разъёмами, системой совместимости и комплектностью. Нужен подбор менеджером."]
        return kit, modules[:3] + supplies[:2]
    module, supply, required = chosen
    kit["items"] = [
        {"product":public_product(module,session["city"]), "quantity":qty,
         "purpose":"Осветить участок. Крепёж, соединение и выключатель указаны в комплектации."},
        {"product":public_product(supply,session["city"]), "quantity":1,
         "purpose":"Питание модулей от готовой розетки."}]
    kit["checks"] = [
        {"name":"Напряжение", "status":"passed", "detail":"24 В у обоих компонентов"},
        {"name":"Мощность", "status":"passed", "detail":f"Требуется не менее {required:g} Вт с запасом 25%; выбран {supply['attributes']['Мощность']} Вт"},
        {"name":"Соединение", "status":"passed", "detail":f"Совпадают система {module['attributes']['Система']} и разъём {module['attributes']['Разъём']}"},
        {"name":"Комплектность", "status":"passed", "detail":"Крепёж, кабели и выключатель включены по данным выбранной системы"}]
    kit["complete"] = True
    return kit, [module,supply]


async def handle_kit(catalog, session, message):
    lower = message.lower()
    # Product questions and purchase conditions are handled by the normal chat.
    if re.search(r"достав|оплат|сертифик|артикул|аналог|demo-", lower):
        return None
    if re.search(r"новый комплект|другая задача", lower):
        session["kit"] = None
    active = session.get("kit")
    starts = bool(re.search(r"подсвет|осве[тщ].*кух|комплект|не знаю.*куп|что.*нужно.*кух", lower))
    if not active and not starts:
        return None
    if not active and not re.search(r"подсвет|кух", lower):
        if os.getenv("OPENAI_API_KEY"):
            return None  # Let AI clarify general tasks without forcing kitchen lighting.
        return {"text":"Опишите задачу. Сейчас пошагово поддерживается подсветка кухни на 1–4 м в сухом месте с готовой розеткой. Для остальных задач доступна консультация по каталогу.",
                "stage":"clarification", "options":["Хочу подсветку кухни"], "engine":"local"}
    if active is None:
        session["kit"] = active = {"length_m":None, "outlet":None, "dry":None, "color":None}
    changes, engine = await extract_kit_slots(message, active)
    changes.update(local_slots(message, active))
    active.update({k:v for k,v in changes.items() if v is not None})
    prompt = question(active)
    if prompt:
        text, options = prompt
        if "не знаю" in lower:
            text = "Если не уверены, не угадывайте: измерьте участок или уточните условие у мастера. " + text
        return {"text":text,"stage":"clarification","options":options,"engine":engine,
                "kit":{"scenario":"kitchen_lighting","answers":dict(active),"complete":False,
                       "items":[],"checks":[],"unresolved":[text],"mode":catalog.mode}}
    kit, products = await build_kit(catalog,session,active)
    if not kit["complete"]:
        return {"text":kit["unresolved"][0],"stage":"clarification","products":products,
                "kit":kit,"engine":engine}
    try:
        proposal = await prepare(catalog,session,[{"id":line["product"]["id"],"quantity":line["quantity"]} for line in kit["items"]])
        expected = {line["product"]["id"]:line["product"] for line in kit["items"]}
        if any(p["attributes"] != expected[p["id"]]["attributes"] or p["warnings"] for p in proposal["items"]):
            session["pending"] = None
            raise CommerceError("Данные комплекта изменились во время подбора. Повторите подбор для проверки совместимости.")
    except CommerceError as exc:
        kit["complete"] = False
        kit["unresolved"] = [str(exc)]
        return {"text":str(exc),"stage":"clarification","kit":kit,"products":products,"engine":engine}
    notice = "Учебный комплект из синтетических товаров. " if catalog.mode == "demo" else ""
    return {"text":notice + "Собрал комплект для согласованных условий. Проверьте состав и количество; корзина изменится только после подтверждения.",
            "stage":"proposal","kit":kit,"products":products,"engine":engine}
