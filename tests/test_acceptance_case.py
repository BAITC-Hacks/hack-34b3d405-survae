"""Regression acceptance: previously failing cases must now pass without xfail."""
import io
import importlib.util

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from app import main
from app.catalog import Catalog


@pytest.fixture
def case_client(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("ALLOW_LOCAL_SETUP", "0")
    monkeypatch.setattr(main, "catalog", Catalog("demo"))
    monkeypatch.setattr(main, "sessions", {})
    with TestClient(main.app) as client:
        client.headers["X-CSRF-Token"] = client.get("/api/cart").json()["csrf"]
        yield client


def test_explicit_lamp_parameters_exclude_incompatible_results(case_client):
    # Supply all three parameters; asking for missing power is valid behavior.
    result = case_client.post("/api/chat", json={"message": "лампа E27 до 40 Вт 3000 К"})
    assert result.status_code == 200
    products = result.json()["products"]
    assert any(p["article"] == "DEMO-LED-W" for p in products)
    assert all(p["attributes"].get("Цоколь") == "E27" and
               p["attributes"].get("Цветовая температура") == "3000 К"
               for p in products), "Products outside the explicit requirements are presented as suitable"


def test_two_line_excel_specification_preserves_both_items(case_client, monkeypatch):
    book = Workbook()
    book.active.append(["DEMO-C16-A", 2])
    book.active.append(["DEMO-LED-A", 3])
    source = io.BytesIO()
    book.save(source)
    uploaded = case_client.post("/api/upload", files={"file": (
        "specification.xlsx", source.getvalue(),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert uploaded.status_code == 200
    received = []

    async def parsed_specification(message, history, products, attachment=None):
        received.append(attachment["text"])
        return {"intent": "propose", "query": "DEMO-C16-A", "quantity": 2,
                "topic": "general", "items": [
                    {"query": "DEMO-C16-A", "quantity": 2},
                    {"query": "DEMO-LED-A", "quantity": 3}]}, "openai"

    # Isolate downstream handling from model accuracy and network availability.
    monkeypatch.setattr(main, "understand", parsed_specification)
    result = case_client.post("/api/chat", json={
        "message": "Подготовь предложение по спецификации",
        "attachment_id": uploaded.json()["attachment_id"]})
    assert result.status_code == 200
    assert "DEMO-C16-A" in received[0] and "DEMO-LED-A" in received[0]
    answer = result.json()
    assert answer["cart"]["count"] == 0
    assert answer["proposal"] is not None
    actual = {line["id"]: line["requested_quantity"] for line in answer["proposal"]["items"]}
    assert actual == {"DEMO-C16-A": 2, "DEMO-LED-A": 3}, "A parsed specification line was silently omitted"
