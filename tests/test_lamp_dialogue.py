"""BE-01: bounded novice dialogue, hard catalog constraints and unchanged cart API."""
import copy

import pytest
from fastapi.testclient import TestClient

from app import main
from app.catalog import Catalog


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("ALLOW_LOCAL_SETUP", "0")
    monkeypatch.setattr(main, "catalog", Catalog("demo"))
    monkeypatch.setattr(main, "sessions", {})
    with TestClient(main.app) as client:
        client.headers["X-CSRF-Token"] = client.get("/api/state").json()["csrf"]
        yield client


def ask(client, message):
    reply = client.post("/api/chat", json={"message":message})
    assert reply.status_code == 200, reply.text
    return reply.json()


def ids(reply):
    return {p["id"] for p in reply["products"]}


def test_complete_constraints_exclude_4000_kelvin(client):
    reply = ask(client, "Нужна лампочка E27, не более 40 Вт, тёплый 3000 К")
    assert ids(reply) == {"DEMO-LED-W"}
    assert reply["engine"] == "local"
    assert reply["cart"]["count"] == 0
    assert reply["proposal"] is None
    assert "полную совместимость" in reply["message"]


def test_novice_turns_preserve_socket_and_maximum(client):
    reply = ask(client, "Хочу купить лампочку, ничего в них не понимаю")
    assert reply["stage"] == "clarification" and "цоколь" in reply["message"]
    assert not reply["products"]
    assert "мощности" in ask(client, "Е 27")["message"]
    assert "оттенок" in ask(client, "40")["message"]
    reply = ask(client, "Тёплый 3000 К")
    assert ids(reply) == {"DEMO-LED-W"}
    assert "не более 40 Вт" in reply["message"]


def test_correct_temperature_does_not_keep_old_cards(client):
    ask(client, "Лампа E27 до 40 Вт 4000 К")
    assert ids(ask(client, "Нет, не 4000 К, а 3000 К")) == {"DEMO-LED-W"}
    assert ids(ask(client, "Теперь 4000 К")) == {"DEMO-LED-A", "DEMO-LED-B", "DEMO-LED-Z"}


def test_socket_correction_never_suggests_e27_for_e14(client):
    ask(client, "Лампа E27 до 40 Вт 3000 К")
    reply = ask(client, "Перепутал: не E27, а E14")
    assert not reply["products"]
    assert "E14" in reply["message"] and "во всём магазине" in reply["message"]


@pytest.mark.parametrize("power,expected", [
    ("до 8 Вт", set()), ("до 9 Вт", {"DEMO-LED-W"}),
    ("9 Вт", {"DEMO-LED-W"}), ("40 Вт", set()),
    ("не больше 40 W", {"DEMO-LED-W"}), ("максимум 8,5 Вт", set()),
])
def test_exact_and_maximum_power_are_different(client, power, expected):
    assert ids(ask(client, f"Лампа E27 {power} 3000 К")) == expected


@pytest.mark.parametrize("correction", ["не 3000 К", "3000 К или 4000 К", "E14 или E27", "0 Вт", "-40 Вт", "3000–4000 К", "5–9 Вт"])
def test_ambiguous_or_invalid_values_require_question(client, correction):
    ask(client, "Лампа E27 до 40 Вт 3000 К")
    reply = ask(client, correction)
    assert reply["stage"] == "clarification"
    assert not reply["products"]


def test_warm_without_numeric_temperature_is_not_guessed(client):
    reply = ask(client, "Лампа E27 до 40 Вт, тёплая")
    assert reply["stage"] == "clarification" and not reply["products"]
    assert ids(ask(client, "3000")) == {"DEMO-LED-W"}


def test_unknown_does_not_invent_socket(client):
    ask(client, "Нужна лампочка")
    reply = ask(client, "Не знаю")
    assert not reply["products"] and "не угадывайте" in reply["message"]


@pytest.mark.parametrize("attribute,value", [
    ("Мощность", "неизвестно"), ("Цветовая температура", "3000–4000 К"),
    ("Цоколь", "E27/E14"), ("Мощность", "0 Вт"),
])
def test_unknown_or_ambiguous_catalog_attribute_not_recommended(client, attribute, value):
    main.catalog.items["DEMO-LED-W"]["attributes"][attribute] = value
    assert not ask(client, "Лампа E27 до 40 Вт 3000 К")["products"]


def test_warned_product_not_recommended(client):
    main.catalog.items["DEMO-LED-W"]["warnings"] = ["Несогласованные данные"]
    assert not ask(client, "Лампа E27 до 40 Вт 3000 К")["products"]


def test_filters_before_limiting_to_five_cards(client):
    # More than five high-relevance but wrong-temperature products precede W.
    for i in range(10):
        p = copy.deepcopy(main.catalog.items["DEMO-LED-A"])
        p.update(id=f"TEST-{i}", article=f"TEST-{i}", name="Лампа E27 3000 модель")
        main.catalog.items[p["id"]] = p
    assert ids(ask(client, "Лампа E27 до 40 Вт 3000 К")) == {"DEMO-LED-W"}


@pytest.mark.parametrize("reset", ["action", "endpoint"])
def test_reset_removes_constraints_but_preserves_cart(client, reset):
    proposal = client.post("/api/chat", json={"action":"propose", "items":[{"id":"DEMO-LED-W", "quantity":1}]}).json()
    accepted = client.post("/api/cart/confirm", json={"proposal_id":proposal["proposal"]["id"], "confirmed":True})
    assert accepted.status_code == 200
    ask(client, "Лампа E27 до 40 Вт 3000 К")
    if reset == "action":
        response = client.post("/api/chat", json={"action":"reset"})
    else:
        response = client.post("/api/chat/reset")
    assert response.status_code == 200
    reply = ask(client, "Нужна лампа")
    assert reply["stage"] == "clarification" and "цоколь" in reply["message"]
    assert not reply["products"] and reply["cart"]["count"] == 1


def test_other_tasks_and_exact_article_still_work(client):
    ask(client, "Лампа E27 до 40 Вт 3000 К")
    assert ids(ask(client, "Есть DEMO-C16-A?")) == {"DEMO-C16-A"}
    assert all(s.get("lamp") is None for s in main.sessions.values())
    assert "счёту" in ask(client, "Какая оплата?")["message"]
    reply = ask(client, "Хочу подсветку кухни")
    assert reply["kit"]["scenario"] == "kitchen_lighting"
    assert all(s.get("lamp") is None for s in main.sessions.values())
    reply = ask(client, "Нужна лампочка")
    assert "цоколь" in reply["message"]
    assert all(s.get("kit") is None for s in main.sessions.values())


def test_lamp_constraints_are_session_local(client):
    ask(client, "Лампа E27 до 40 Вт 3000 К")
    with TestClient(main.app) as other:
        other.headers["X-CSRF-Token"] = other.get("/api/state").json()["csrf"]
        reply = ask(other, "Нужна лампочка")
        assert "цоколь" in reply["message"] and not reply["products"]


def test_ai_enabled_cannot_bypass_hard_constraints(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-not-a-real-key")
    async def unexpected(*args, **kwargs):
        pytest.fail("Bounded explicit constraints must not be rewritten by the LLM")
    monkeypatch.setattr(main, "understand", unexpected)
    monkeypatch.setattr(main, "compose_reply", unexpected)
    assert ids(ask(client, "Лампа E27 до 40 Вт 3000 К")) == {"DEMO-LED-W"}


def test_selection_never_changes_cart_and_new_message_invalidates_proposal(client):
    reply = ask(client, "Лампа E27 до 40 Вт 3000 К")
    proposal = client.post("/api/chat", json={"action":"propose", "items":[{"id":reply["products"][0]["id"], "quantity":1}]}).json()
    pending = proposal["proposal"]["id"]
    assert ask(client, "да")["cart"]["count"] == 0
    reply = ask(client, "Нет, нужна E14")
    assert reply["proposal"] is None and not reply["products"]
    assert client.post("/api/cart/confirm", json={"proposal_id":pending, "confirmed":True}).status_code == 409
    assert client.get("/api/cart").json()["count"] == 0
