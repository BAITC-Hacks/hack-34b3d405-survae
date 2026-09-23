"""Regression acceptance: previously failing cases must now pass without xfail."""
import importlib.util

import pytest
from fastapi.testclient import TestClient

from app import main
from app.catalog import Catalog

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("app.lamps") is None,
    reason="BE-01 is not present on this checkout; run against PR #6")


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("ALLOW_LOCAL_SETUP", "0")
    monkeypatch.setattr(main, "catalog", Catalog("demo"))
    monkeypatch.setattr(main, "sessions", {})
    with TestClient(main.app) as client:
        client.headers["X-CSRF-Token"] = client.get("/api/cart").json()["csrf"]
        yield client


def ask(client, message):
    reply = client.post("/api/chat", json={"message": message})
    assert reply.status_code == 200
    return reply.json()


def test_minimum_order_question_after_lamp_selection(client):
    selected = ask(client, "Лампа E27 до 40 Вт 3000 К")
    assert [p["article"] for p in selected["products"]] == ["DEMO-LED-W"]
    reply = ask(client, "Какая минимальная партия?")
    assert reply["cart"]["count"] == 0
    assert "Минимальная партия" in reply["message"]
    assert "DEMO-LED-W: 1 ед." in reply["message"]


def test_exact_wattage_reply_never_becomes_a_maximum(client):
    ask(client, "Нужна лампочка")
    ask(client, "E27")
    ask(client, "Нужна ровно 40 Вт")
    reply = ask(client, "3000 К")
    assert reply["cart"]["count"] == 0
    # Demo has only 9 W E27 bulbs; they cannot satisfy an exact 40 W request.
    assert not reply["products"], "A 9 W bulb was returned for an explicit exact 40 W request"
    assert "не более 40 Вт" not in reply["message"]
