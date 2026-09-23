"""Conversation memory and atomic specification handling, without network calls."""
import asyncio
import io

import httpx
import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from app import ai, main
from app.catalog import Catalog
from app.dialogue import HISTORY_MESSAGES, remember
from app.attachments import extract, tabular_items


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("ALLOW_LOCAL_SETUP", "0")
    monkeypatch.setattr(main, "catalog", Catalog("demo"))
    monkeypatch.setattr(main, "sessions", {})
    with TestClient(main.app) as client:
        client.headers["X-CSRF-Token"] = client.get("/api/cart").json()["csrf"]
        yield client


def ask(client, message, **extra):
    reply = client.post("/api/chat", json={"message":message, **extra})
    assert reply.status_code == 200, reply.text
    return reply.json()


def fake_spec(monkeypatch, items, intent="propose"):
    async def understand(*args):
        return {"intent":intent, "route":"general", "query":"DEMO-C16-A",
                "quantity":2, "topic":"general", "items":items}, "openai"
    monkeypatch.setattr(main, "understand", understand)
    async def explain(*args):
        return "Для наблюдения нужен экран, связанный с контроллером. Проверьте модель контроллера по маркировке."
    monkeypatch.setattr(main, "compose_reply", explain)


def test_erroneous_new_topic_never_erases_industry_or_original_request(client, monkeypatch):
    seen = []
    async def understand(message, history, products, attachment=None):
        seen.append(list(history))
        return {"intent":"clarify", "route":"general", "new_topic":True,
                "question":f"Уточнение {len(seen)}?", "query":"", "topic":"general"}, "openai"
    monkeypatch.setattr(main, "understand", understand)
    messages = ["хочу панели оператора", "следить за механизмами", "промышленное оборудование",
                "пищевая и химическая индустрия", "панель оператора"]
    for message in messages:
        assert ask(client, message)["cart"]["count"] == 0
    assert [m["text"] for m in seen[-1] if m["role"] == "user"] == messages[:-1]
    ask(client, "", action="reset")
    assert next(iter(main.sessions.values()))["history"] == []


def test_history_is_bounded_but_keeps_complete_turns():
    session = {"history":[]}
    for n in range(100):
        remember(session, f"user {n}", f"answer {n}")
    assert len(session["history"]) == HISTORY_MESSAGES
    assert [e["role"] for e in session["history"]] == ["user", "assistant"] * (HISTORY_MESSAGES // 2)
    assert session["history"][-1]["text"] == "answer 99"


@pytest.mark.parametrize("ai_mode", [False, True])
def test_commercial_question_keeps_selection_and_allows_parameter_correction(client, monkeypatch, ai_mode):
    ask(client, "Лампа E27 до 40 Вт 3000 К")
    if ai_mode:
        async def understand(message, history, products, attachment=None):
            return {"intent":"conditions", "route":"general", "new_topic":True,
                    "query":"", "topic":"minimum"}, "openai"
        monkeypatch.setattr(main, "understand", understand)
    reply = ask(client, "Какая минимальная партия?")
    assert "DEMO-LED-W: 1 ед." in reply["message"]
    assert next(iter(main.sessions.values()))["lamp"]["temperature"] == 3000
    if not ai_mode:
        corrected = ask(client, "Теперь 4000 К")
        assert {p["article"] for p in corrected["products"]} == {"DEMO-LED-A", "DEMO-LED-B", "DEMO-LED-Z"}


@pytest.mark.parametrize("power", ["ровно 40 Вт", "именно 40 Вт", "точно 40 Вт"])
def test_explicit_exact_power_overrides_question_wording(client, power):
    ask(client, "Нужна лампочка E27")
    ask(client, power)
    reply = ask(client, "3000 К")
    assert not reply["products"]
    assert "ровно 40 Вт" in reply["message"]


def test_multi_item_proposal_is_confirmed_as_one_atomic_order(client, monkeypatch):
    fake_spec(monkeypatch, [{"query":"DEMO-C16-A", "quantity":2}, {"query":"DEMO-LED-A", "quantity":3}])
    reply = ask(client, "Подготовь предложение по списку")
    assert reply["stage"] == "proposal" and reply["cart"]["count"] == 0
    assert {p["id"]:p["requested_quantity"] for p in reply["proposal"]["items"]} == {"DEMO-C16-A":2, "DEMO-LED-A":3}
    body = {"proposal_id":reply["proposal"]["id"], "confirmed":True}
    for _ in range(2):
        confirmed = client.post("/api/cart/confirm", json=body)
        assert confirmed.status_code == 200
        assert confirmed.json()["cart"]["count"] == 5


@pytest.mark.parametrize("bad_item,expected", [
    ({"query":"DEMO-UNKNOWN", "quantity":3}, "нет однозначного"),
    ({"query":"лампа", "quantity":3}, "нет однозначного"),
    ({"query":"DEMO-LED-A", "quantity":None}, "количество"),
    ({"query":"DEMO-LED-A", "quantity":0}, "Некорректная строка"),
    ({"query":"DEMO-LED-A", "quantity":-3}, "Некорректная строка"),
    ({"query":"DEMO-LED-A", "quantity":3, "confirmed":True}, "Некорректная строка"),
])
def test_unresolved_spec_line_never_becomes_a_partial_order(client, monkeypatch, bad_item, expected):
    old = ask(client, "", action="propose", items=[{"id":"DEMO-C16-A", "quantity":1}])["proposal"]["id"]
    fake_spec(monkeypatch, [{"query":"DEMO-C16-A", "quantity":2}, bad_item])
    result = ask(client, "Подготовь весь список")
    assert "1. DEMO-C16-A" in result["message"] and "2." in result["message"]
    assert expected in result["message"]
    assert result["proposal"] is None and result["cart"]["count"] == 0
    assert client.post("/api/cart/confirm", json={"proposal_id":old, "confirmed":True}).status_code == 409


@pytest.mark.parametrize("quantity", [10000, 0.5])
def test_invalid_stock_or_minimum_in_second_line_does_not_order_first(client, monkeypatch, quantity):
    fake_spec(monkeypatch, [{"query":"DEMO-C16-A", "quantity":2}, {"query":"DEMO-LED-A", "quantity":quantity}])
    result = ask(client, "Подготовь список")
    assert "Предложение не создано" in result["message"]
    assert "1. DEMO-C16-A" in result["message"] and "2. DEMO-LED-A" in result["message"]
    assert result["proposal"] is None and result["cart"]["count"] == 0


def test_duplicate_spec_rows_are_aggregated_without_losing_quantities(client, monkeypatch):
    fake_spec(monkeypatch, [{"query":"DEMO-C16-A", "quantity":2}, {"query":"DEMO-C16-A", "quantity":3}])
    result = ask(client, "Подготовь список")
    assert len(result["proposal"]["items"]) == 1
    assert result["proposal"]["items"][0]["requested_quantity"] == 5


def test_specification_review_is_not_purchase_intent(client, monkeypatch):
    fake_spec(monkeypatch, [{"query":"DEMO-C16-A", "quantity":2}, {"query":"DEMO-LED-A", "quantity":3}], "search")
    result = ask(client, "Проверь наличие по списку")
    assert len(result["products"]) == 2
    assert result["proposal"] is None and result["cart"]["count"] == 0


def test_oversize_specification_not_silently_truncated(client, monkeypatch):
    fake_spec(monkeypatch, [{"query":"DEMO-C16-A", "quantity":1}] * 13)
    result = ask(client, "Подготовь список")
    assert result["proposal"] is None and "до 12 строк" in result["message"]


def test_foreign_attachment_is_rejected_before_model_or_cart_change(client, monkeypatch):
    async def forbidden(*args):
        raise AssertionError("Invalid attachment must not reach the model")
    monkeypatch.setattr(main, "understand", forbidden)
    result = client.post("/api/chat", json={"message":"Подготовь заказ", "attachment_id":"unknown"})
    assert result.status_code == 400
    assert client.get("/api/cart").json()["count"] == 0


def test_responses_payload_replays_roles_and_original_user_facts(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-placeholder")
    history = [{"role":"user", "text":"Промышленная панель"}, {"role":"assistant", "text":"Для какой отрасли?"}]
    plan = ai.ShoppingIntent(intent="consult", query="", answer="Общее пояснение", topic="general", language="ru")
    class FakeClient:
        def __init__(self, **kwargs): pass
        async def aclose(self): pass
        async def post(self, url, **kwargs):
            payload = kwargs["json"]
            assert payload["store"] is False
            assert payload["input"][:2] == [{"role":h["role"], "content":h["text"]} for h in history]
            assert payload["input"][-1]["content"][0]["text"] == "Химическая промышленность"
            assert "Промышленная панель" not in payload["instructions"]
            return httpx.Response(200, request=httpx.Request("POST", url), json={"status":"completed",
                "output":[{"content":[{"type":"output_text", "text":plan.model_dump_json()}]}]})
    monkeypatch.setattr(ai.httpx, "AsyncClient", FakeClient)
    _, engine = asyncio.run(ai.understand("Химическая промышленность", history, []))
    assert engine == "openai"


def spreadsheet(rows):
    book = Workbook()
    for row in rows:
        book.active.append(row)
    output = io.BytesIO()
    book.save(output)
    book.close()
    return output.getvalue()


@pytest.mark.parametrize("second", ["DEMO-LED-A", "UNKNOWN-SKU"])
def test_excel_cells_prevent_model_from_dropping_second_row(client, monkeypatch, second):
    source = spreadsheet([["Артикул", "Количество"], ["DEMO-C16-A", 2], [second, 3]])
    upload = client.post("/api/upload", files={"file":("order.xlsx", source)})
    assert upload.status_code == 200
    fake_spec(monkeypatch, [{"query":"DEMO-C16-A", "quantity":2}])
    reply = ask(client, "Подготовь предложение", attachment_id=upload.json()["attachment_id"])
    assert second in reply["message"]
    assert reply["cart"]["count"] == 0
    if second == "DEMO-LED-A":
        assert len(reply["proposal"]["items"]) == 2
    else:
        assert reply["proposal"] is None and "Частичное предложение не создаю" in reply["message"]


def test_spreadsheet_zero_quantity_is_not_erased():
    result = extract("zero.xlsx", spreadsheet([["DEMO-C16-A", 0]]))
    assert "0" in result["text"]
    assert result["structured_items"] == [{"query":"DEMO-C16-A", "quantity":0}]


@pytest.mark.parametrize("rows", [151, 200])
def test_excel_row_overflow_rejected_instead_of_truncation(rows):
    with pytest.raises(ValueError, match="150 строк"):
        extract("long.xlsx", spreadsheet([["DEMO-C16-A", 1]] * rows))


def test_text_overflow_is_rejected_instead_of_losing_lines():
    with pytest.raises(ValueError, match="16 000"):
        extract("long.txt", b"x" * 16001)


@pytest.mark.parametrize("body", [b" ", b"\n\n\t"])
def test_blank_documents_not_sent_to_model(body):
    with pytest.raises(ValueError, match="не найден текст"):
        extract("blank.txt", body)


def test_ambiguous_table_not_misinterpreted_as_two_column_list():
    assert tabular_items([["DEMO-C16-A", "DEMO-LED-A", 3]]) is None


@pytest.mark.parametrize("bad_advice", ["Какое оборудование?", None])
def test_repeated_questions_fail_honestly_if_model_cannot_continue(client, monkeypatch, bad_advice):
    async def understand(*args):
        return {"intent":"clarify", "query":"", "question":"Какое оборудование?", "topic":"general"}, "openai"
    async def compose(*args):
        if bad_advice is None:
            raise httpx.TimeoutException("provider detail must not leak")
        return bad_advice
    monkeypatch.setattr(main, "understand", understand)
    monkeypatch.setattr(main, "compose_reply", compose)
    ask(client, "Панель оператора")
    reply = ask(client, "Промышленное оборудование")
    assert reply["engine"] == "fallback" and reply["stage"] == "answer"
    assert "Какое оборудование?" not in reply["message"]
    assert "provider" not in reply["message"]
    assert reply["cart"]["count"] == 0


def test_missing_catalog_results_are_not_rewritten_as_missing_store_inventory(client, monkeypatch):
    async def understand(*args):
        return {"intent":"search", "query":"UNKNOWN-OPERATOR-PANEL", "topic":"general"}, "openai"
    async def compose(*args):
        pytest.fail("An empty sample must not be paraphrased into a store-wide inventory claim")
    monkeypatch.setattr(main, "understand", understand)
    monkeypatch.setattr(main, "compose_reply", compose)
    reply = ask(client, "Панель оператора")
    assert "В подключённой выборке" in reply["message"]
    assert not reply["products"]


def test_classifier_new_topic_flag_does_not_break_explicit_selected_product(client, monkeypatch):
    ask(client, "Лампа E27 до 40 Вт 3000 К")
    async def understand(*args):
        return {"intent":"propose", "route":"general", "new_topic":True, "product_id":"DEMO-LED-W",
                "quantity":1, "query":"", "topic":"general"}, "openai"
    monkeypatch.setattr(main, "understand", understand)
    reply = ask(client, "Добавь эту одну штуку")
    assert reply["proposal"]["items"][0]["id"] == "DEMO-LED-W"
    assert reply["cart"]["count"] == 0


def test_used_attachment_cannot_be_replayed(client, monkeypatch):
    source = spreadsheet([["DEMO-C16-A", 2]])
    upload = client.post("/api/upload", files={"file":("order.xlsx", source)})
    fake_spec(monkeypatch, [{"query":"DEMO-C16-A", "quantity":2}])
    token = upload.json()["attachment_id"]
    ask(client, "Подготовь предложение", attachment_id=token)
    response = client.post("/api/chat", json={"message":"Ещё раз", "attachment_id":token})
    assert response.status_code == 400
    assert client.get("/api/cart").json()["count"] == 0


def test_literal_parameter_answers_survive_wrong_ai_new_topic_and_route(client, monkeypatch):
    async def understand(message, history, products, attachment=None):
        return {"intent":"search", "route":"lamp" if not history else "general", "new_topic":True,
                "query":message, "topic":"general"}, "openai"
    async def compose(message, history, facts):
        return facts["result"]["message"]
    monkeypatch.setattr(main, "understand", understand)
    monkeypatch.setattr(main, "compose_reply", compose)
    for message in ["Нужна лампочка", "E27", "Ровно 40 Вт"]:
        assert ask(client, message)["stage"] == "clarification"
    reply = ask(client, "3000 К")
    assert not reply["products"] and "ровно 40 Вт" in reply["message"]


def test_verified_lamp_no_match_message_cannot_be_overwritten(client, monkeypatch):
    async def understand(*args):
        return {"intent":"search", "route":"lamp", "new_topic":True, "query":"", "topic":"general"}, "openai"
    async def compose(*args):
        pytest.fail("Do not rewrite a verified no-match result")
    monkeypatch.setattr(main, "understand", understand)
    monkeypatch.setattr(main, "compose_reply", compose)
    reply = ask(client, "Лампа E27 ровно 40 Вт 3000 К")
    assert not reply["products"]
    assert "это не означает, что товара нет во всём магазине" in reply["message"]


def test_single_lamp_misclassified_as_one_spec_item_still_uses_strict_filter(client, monkeypatch):
    async def understand(*args):
        return {"intent":"search", "route":"general", "new_topic":True,
                "query":"E27 40W 3000K", "topic":"general",
                "items":[{"query":"E27 40W 3000K", "quantity":None}]}, "openai"
    monkeypatch.setattr(main, "understand", understand)
    reply = ask(client, "Лампа E27 до 40 Вт 3000 К")
    assert {p["article"] for p in reply["products"]} == {"DEMO-LED-W"}
    assert reply["proposal"] is None


def test_short_kitchen_answer_overrules_erroneous_consult_intent(client, monkeypatch):
    ask(client, "Хочу подсветку кухни")
    async def understand(*args):
        return {"intent":"consult", "route":"general", "new_topic":True,
                "query":"", "topic":"general", "answer":"Общий ответ вместо длины"}, "openai"
    async def compose(message, history, facts):
        return facts["result"]["message"]
    monkeypatch.setattr(main, "understand", understand)
    monkeypatch.setattr(main, "compose_reply", compose)
    reply = ask(client, "2 м")
    assert reply["kit"]["answers"]["length_m"] == 2
    assert reply["stage"] == "clarification"
