"""Bounded lamp dialogue: explicit user constraints, never model-invented facts.

This checks three catalog attributes, not full electrical compatibility. Unknown
or ambiguous values require clarification; they must not widen recommendations.
"""
import re

from app.catalog import search_score


SOCKET = r"(?<!\w)(?:[eе]\s*\d{2}|gu\s*\d{1,2}|g\s*\d{1,2}|gx\s*\d{2})(?!\w)"
TEMPERATURE = r"(?<!\d)([+-]?\d{4})\s*(?:[kк]|кельвин\w*)(?!\w)"
POWER = r"(?<![\d.,])([+-]?\d+(?:[.,]\d+)?)\s*(?:вт|w|ватт\w*)(?!\w)"


def socket(value):
    return re.sub(r"\s+", "", value.lower()).replace("е", "e").upper()


def negated(text, match):
    return bool(re.search(r"\b(?:не|вместо)\s*$", text[:match.start()]))


def read_changes(text, state):
    """Only literal slots are accepted. Multiple values are intentionally refused."""
    changes, ambiguous = {}, []
    for key, pattern, convert in [
        ("socket", SOCKET, lambda m: socket(m.group())),
        ("temperature", TEMPERATURE, lambda m: int(m.group(1))),
        ("power", POWER, lambda m: float(m.group(1).replace(",", "."))),
    ]:
        matches = list(re.finditer(pattern, text))
        positive = [m for m in matches if not negated(text, m)]
        values = {convert(m) for m in positive}
        if key != "socket" and any(re.search(r"\d\s*[-–—/]\s*$", text[:m.start()]) for m in matches):
            values = set()  # A range is not a single confirmed parameter.
        if len(values) == 1:
            changes[key] = values.pop()
            if key == "power":
                prefix = text[max(0, positive[0].start()-35):positive[0].start()]
                maximum = re.search(r"(?:\bдо|не более|не больше|максим\w*|макс\.?|предел)\s*$", prefix)
                changes["power_mode"] = "max" if maximum or state.get("awaiting") == "power" else "exact"
        elif matches:
            changes[key] = None
            ambiguous.append(key)
    # Bare replies are allowed only to the specific question just asked.
    if state.get("awaiting") == "power" and re.fullmatch(r"\d+(?:[.,]\d+)?", text):
        changes.update(power=float(text.replace(",", ".")), power_mode="max")
    if state.get("awaiting") == "temperature" and re.fullmatch(r"\d{4}", text):
        changes["temperature"] = int(text)
    # "Warm" is not assumed to mean precisely 3000 K. Ask the user to choose.
    if "temperature" not in changes and re.search(r"тепл|нейтрал|холод", text):
        changes["temperature"] = None
    if changes.get("power") is not None and not 0 < changes["power"] <= 10000:
        changes["power"] = None
        ambiguous.append("power")
    if changes.get("temperature") is not None and not 1000 <= changes["temperature"] <= 20000:
        changes["temperature"] = None
        ambiguous.append("temperature")
    return changes, ambiguous


def number_attribute(value, unit):
    match = re.fullmatch(r"\s*(\d+(?:[.,]\d+)?)\s*(?:" + unit + r")?\s*", str(value), re.I)
    return float(match.group(1).replace(",", ".")) if match else None


def matches_constraints(product, state):
    a = product["attributes"]
    watts = number_attribute(a.get("Мощность"), r"вт|w|ватт")
    kelvin = number_attribute(a.get("Цветовая температура"), r"к|k")
    if product["warnings"] or watts is None or watts <= 0 or kelvin is None:
        return False
    if socket(str(a.get("Цоколь", ""))) != state["socket"] or kelvin != state["temperature"]:
        return False
    return watts <= state["power"] if state["power_mode"] == "max" else watts == state["power"]


def question(state):
    if not state.get("socket"):
        return "socket", "Какой цоколь указан на старой лампочке или светильнике — например E27 или E14? Это тип основания лампы. Если маркировка неизвестна, не угадывайте: уточните её по инструкции или у специалиста.", ["E27", "E14", "Не знаю цоколь"]
    if not state.get("power"):
        return "power", "Какой предел мощности указан на светильнике (например, «не более 40 Вт»)? Речь о потребляемой мощности, не об эквиваленте лампы накаливания. Напишите значение с «Вт»; если не знаете — уточните маркировку.", []
    if not state.get("temperature"):
        return "temperature", "Какой оттенок света нужен? 3000 К — тёплый, 4000 К — нейтральный. Выберите температуру или укажите другую с буквой К.", ["Тёплый 3000 К", "Нейтральный 4000 К"]
    return None


async def handle_lamp(catalog, session, message):
    text = message.lower().replace("ё", "е").strip()
    starts = bool(re.search(r"ламп(?:а|у|ы|очка|очку|очки|очек)\b", text) or re.search(SOCKET, text))
    # Other tasks explicitly leave this bounded dialogue; no stale constraints.
    if re.search(r"подсвет|кабел|автомат|розет|щиток|другая задача|новая задача", text):
        session["lamp"] = None
        return None
    # Exact article/details and commercial questions keep their existing handlers.
    if re.search(r"артикул|demo-", text) or any(search_score(p, message) >= 100 for p in catalog.items.values()):
        session["lamp"] = None
        return None
    if re.search(r"достав|оплат|сертифик|аналог", text):
        return None
    if not starts and not session.get("lamp"):
        return None
    if starts:
        session["kit"] = None
    state = session.get("lamp")
    if state is None or re.search(r"другая ламп|новый подбор", text):
        state = session["lamp"] = {}
    changes, ambiguous = read_changes(text, state)
    state.update(changes)
    # Never reuse cards from an earlier, now incompatible selection.
    session["last_products"] = []
    prompt = question(state)
    if prompt:
        state["awaiting"], message, options = prompt
        prefix = "Не могу однозначно определить параметр. " if ambiguous else ""
        session["ai_clarification"] = True
        return {"text":prefix + message, "stage":"clarification", "options":options, "engine":"local"}
    state["awaiting"] = None
    session["ai_clarification"] = False
    # Deliberately bounded retrieval. Filter AFTER retrieving beyond the old top-5.
    query = f"лампа {state['socket']} {state['temperature']}"
    candidates = await catalog.search(query, limit=40)
    chosen = [p for p in candidates if matches_constraints(p, state)][:5]
    session["last_products"] = chosen
    limit = "не более" if state["power_mode"] == "max" else "ровно"
    criteria = f"{state['socket']}, {state['temperature']} К, {limit} {state['power']:g} Вт"
    if not chosen:
        message = f"Среди проверенных позиций подключённой выборки нет лампы с параметрами: {criteria}. Условия автоматически не меняю. Можно уточнить параметры или обратиться к менеджеру — это не означает, что товара нет во всём магазине."
        if catalog.loading:
            message += " Каталог ещё загружается; повторите запрос позже."
        if catalog.error:
            message += " Не удалось полностью получить данные каталога."
    else:
        message = f"По данным каталога совпадают три указанных параметра: {criteria}. Наличие показано для города {session['city']}. Ещё проверьте напряжение, размеры и требования светильника по его инструкции: полную совместимость по этим трём параметрам подтвердить нельзя. Выберите карточку и количество; добавление будет только после подтверждения."
    return {"text":message, "products":chosen, "stage":"answer", "engine":"local"}
