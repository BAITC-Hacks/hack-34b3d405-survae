import asyncio
import secrets
import time
from decimal import Decimal

from app.catalog import public_product, stock_for


class CommerceError(Exception):
    pass


def new_session():
    return {"csrf": secrets.token_urlsafe(24), "city": "Астана", "cart": {}, "pending": None,
            "completed": [], "history": [], "last_products": [], "attachments": {},
            "kit": None, "lamp": None, "lock": asyncio.Lock(), "touched": time.time()}


def cart_view(session):
    items = [{**line, "total": float(Decimal(str(line["price"])) * Decimal(str(line["cart_quantity"])))}
             for line in session["cart"].values()]
    return {"items": items, "total": float(sum(Decimal(str(p["total"])) for p in items)),
            "count": sum(p["cart_quantity"] for p in items), "url": "/cart",
            "kind": "demo", "notice": "Демонстрационная корзина. Заказы на ekt.kz не оформляются."}


async def prepare(catalog, session, requested, operation="add"):
    # A failed new selection must not leave an old proposal confirmable.
    session["pending"] = None
    if operation not in {"add", "remove", "clear"}:
        raise CommerceError("Неизвестная операция.")
    if not requested and operation != "clear":
        raise CommerceError("Сначала выберите товар.")
    lines = []
    if operation == "add":
        combined = {}
        for request in requested:
            product_id = str(request["id"])
            try:
                quantity = Decimal(str(request["quantity"]))
            except Exception:
                raise CommerceError("Укажите корректное количество.")
            if not quantity.is_finite() or quantity <= 0 or quantity > 1000000:
                raise CommerceError("Количество должно быть положительным и не больше 1 000 000.")
            combined[product_id] = combined.get(product_id, Decimal(0)) + quantity
        for product_id, quantity in combined.items():
            product = await catalog.detail(product_id, fresh=True)
            if not product or product["price"] is None:
                raise CommerceError("Не удалось подтвердить товар и цену. Корзина не изменена.")
            available = stock_for(product, session["city"])
            existing = session["cart"].get(product_id, {}).get("cart_quantity", 0)
            if available is None:
                raise CommerceError(f"Нет подтверждённого остатка в городе {session['city']}. Корзина не изменена.")
            if quantity + Decimal(str(existing)) > Decimal(str(available)):
                raise CommerceError(f"В городе {session['city']} доступно {available:g}, в корзине уже {existing:g}. Уменьшите количество.")
            minimum = Decimal(str(product["minimum"]))
            if quantity < minimum or quantity % minimum != 0:
                raise CommerceError(f"Количество должно быть кратно минимальной партии: {minimum:g}.")
            lines.append({**public_product(product, session["city"]), "requested_quantity":float(quantity)})
    elif operation == "remove":
        for request in requested:
            line = session["cart"].get(str(request["id"]))
            if line:
                lines.append({**line,"requested_quantity":line["cart_quantity"]})
        if not lines:
            raise CommerceError("Этого товара уже нет в корзине.")
    else:
        lines = [{**p,"requested_quantity":p["cart_quantity"]} for p in session["cart"].values()]
        if not lines:
            raise CommerceError("Корзина уже пуста.")
    proposal = {"id":secrets.token_urlsafe(20),"operation":operation,"items":lines,
                "city":session["city"],"expires_at":time.time()+300,
                "total":float(sum(Decimal(str(p["price"]))*Decimal(str(p["requested_quantity"])) for p in lines))}
    session["pending"] = proposal
    return proposal


async def confirm(catalog, session, action_id):
    if action_id in session["completed"]:
        return "Это действие уже выполнено. Повторного изменения корзины нет."
    proposal = session["pending"]
    if not proposal or not secrets.compare_digest(proposal["id"], str(action_id)):
        raise CommerceError("Подтверждение не относится к текущему предложению.")
    if proposal["expires_at"] < time.time() or proposal["city"] != session["city"]:
        session["pending"] = None
        raise CommerceError("Предложение устарело. Выберите товары ещё раз.")
    if proposal["operation"] == "add":
        updated = []
        for line in proposal["items"]:
            product = await catalog.detail(line["id"], fresh=True)
            if not product:
                raise CommerceError("Не удалось повторно проверить остатки. Корзина не изменена.")
            stock = stock_for(product, session["city"])
            qty = Decimal(str(line["requested_quantity"]))
            existing = Decimal(str(session["cart"].get(line["id"],{}).get("cart_quantity",0)))
            if stock is None or qty+existing > Decimal(str(stock)):
                session["pending"] = None
                raise CommerceError("Остаток изменился или не подтверждён. Корзина не изменена; выберите количество снова.")
            if product["price"] != line["price"] or product["minimum"] != line["minimum"]:
                session["pending"] = None
                raise CommerceError("Цена или минимальная партия изменились. Требуется новое подтверждение.")
            if product["attributes"] != line["attributes"] or product["warnings"] != line["warnings"]:
                session["pending"] = None
                raise CommerceError("Характеристики товара изменились. Проверьте совместимость и создайте новое предложение.")
            updated.append({**public_product(product,session["city"]),"cart_quantity":float(qty+existing)})
        for product in updated:
            session["cart"][product["id"]] = product
        message = "Готово. Товары добавлены в демонстрационную корзину после вашего подтверждения."
    elif proposal["operation"] == "remove":
        for line in proposal["items"]:
            session["cart"].pop(line["id"],None)
        message = "Товар удалён из корзины после вашего подтверждения."
    else:
        session["cart"].clear()
        message = "Корзина очищена после вашего подтверждения."
    session["completed"] = (session["completed"] + [action_id])[-50:]
    session["pending"] = None
    return message
