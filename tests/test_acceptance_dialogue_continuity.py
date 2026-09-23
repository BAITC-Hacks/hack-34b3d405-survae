"""Regression acceptance: previously failing cases must now pass without xfail."""
import importlib.util

import pytest
from fastapi.testclient import TestClient

from app import main
from app.catalog import Catalog


def test_short_offered_answer_preserves_kitchen_context(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("ALLOW_LOCAL_SETUP", "0")
    monkeypatch.setattr(main, "catalog", Catalog("demo"))
    monkeypatch.setattr(main, "sessions", {})
    with TestClient(main.app) as client:
        client.headers["X-CSRF-Token"] = client.get("/api/cart").json()["csrf"]
        first = client.post("/api/chat", json={"message": "Хочу подсветку кухни"})
        assert first.status_code == 200
        assert "2 м" in first.json()["options"]

        async def ambiguous_router(message, history, products, attachment=None):
            # Exercise a valid but unhelpful model route, without network access.
            return {"intent": "clarify", "route": "general", "new_topic": False,
                    "query": "", "topic": "general", "question":
                    "Вы хотите осветить 2 метра. Это будет для светодиодной ленты?"}, "openai"

        monkeypatch.setattr(main, "understand", ambiguous_router)
        second = client.post("/api/chat", json={"message": "2 м"})
        assert second.status_code == 200
        answer = second.json()
        assert answer["cart"]["count"] == 0
        assert answer["kit"] is not None, "A server-offered length answer discarded the kit"
        assert answer["kit"]["answers"]["length_m"] == 2
