"""Resolve every specification line; never silently order a partial match."""
import re

from pydantic import ValidationError

from app.ai import IntentItem
from app.commerce import CommerceError, prepare


def exact_match(product, query):
    text = query.strip().casefold()
    if text == product["name"].strip().casefold():
        return True
    return any(value and re.search(r"(?<![\w-])" + re.escape(str(value).casefold()) + r"(?![\w-])", text)
               for value in (product.get("id"), product.get("article"), product.get("supplier_article")))


async def resolve_specification(catalog, session, items, *, propose):
    session["pending"] = None
    if len(items) > 12:
        return {"text":"В одном предложении можно проверить до 12 строк. Разделите спецификацию на части; ни одна строка не добавлена.",
                "stage":"clarification"}
    report, selected, requested, unresolved = [], {}, [], False
    for number, raw in enumerate(items, 1):
        try:
            item = IntentItem.model_validate(raw)
        except ValidationError:
            report.append(f"{number}. Некорректная строка: нужны название/артикул и положительное количество.")
            unresolved = True
            continue
        candidates = await catalog.search(item.query, limit=8)
        exact = [p for p in candidates if exact_match(p, item.query)]
        label = item.query[:120].replace("\n", " ")
        if len(exact) != 1:
            unresolved = True
            reason = "нет однозначного совпадения в подключённой выборке" if not exact else "есть несколько совпадений"
            report.append(f"{number}. {label}: {reason}. Уточните артикул; замена автоматически не выбрана.")
            continue
        product = exact[0]
        selected[product["id"]] = product
        if item.quantity is None:
            report.append(f"{number}. {product['article']}: найден, уточните количество.")
            unresolved = True
        else:
            report.append(f"{number}. {product['article']} — {item.quantity:g} ед.")
            requested.append({"id":product["id"], "quantity":item.quantity})
    session["last_products"] = list(selected.values())
    text = f"Проверил строки спецификации: {len(items)}.\n" + "\n".join(report)
    stage = "clarification" if unresolved else "answer"
    if unresolved:
        text += "\nЕсть неуточнённые строки. Частичное предложение не создаю; корзина не изменилась."
    elif propose:
        try:
            await prepare(catalog, session, requested)
        except CommerceError as exc:
            text += f"\nПредложение не создано: {exc} Все строки сохранены в отчёте выше."
        else:
            stage = "proposal"
            text += "\nПроверьте каждую строку, количество и сумму. Корзина изменится только после подтверждения кнопкой."
    else:
        text += "\nЭто проверка списка, не добавление в корзину."
    return {"text":text, "products":list(selected.values()), "stage":stage}
