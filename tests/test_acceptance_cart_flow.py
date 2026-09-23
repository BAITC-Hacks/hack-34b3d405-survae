"""Independent checkout acceptance, local rules and synthetic catalog only.

This does not verify real AI routing or an order on ekt.kz.
"""
from fastapi.testclient import TestClient

from app import main
from app.catalog import Catalog


def test_kitchen_confirmation_replay_and_reset_preserve_cart(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("ALLOW_LOCAL_SETUP", "0")
    monkeypatch.setattr(main, "catalog", Catalog("demo"))
    monkeypatch.setattr(main, "sessions", {})
    with TestClient(main.app) as client:
        client.headers["X-CSRF-Token"] = client.get("/api/cart").json()["csrf"]
        for message in ["Хочу подсветку кухни", "2 м", "Есть розетка рядом",
                        "Сухое место, вдали от воды", "Тёплый свет"]:
            reply = client.post("/api/chat", json={"message": message})
            assert reply.status_code == 200
            assert reply.json()["cart"]["count"] == 0
        proposal = reply.json()["proposal"]
        assert proposal is not None
        assert {p["id"]: p["requested_quantity"] for p in proposal["items"]} == {
            "DEMO-BAR-W": 2, "DEMO-PSU-60": 1}
        # A plain-language acknowledgement cannot commit the order.
        said_yes = client.post("/api/chat", json={"message": "да, добавь"})
        assert said_yes.status_code == 200
        assert said_yes.json()["cart"]["count"] == 0
        for _ in range(2):
            confirmed = client.post("/api/cart/confirm", json={
                "proposal_id": proposal["id"], "confirmed": True})
            assert confirmed.status_code == 200
            assert confirmed.json()["cart"]["count"] == 3
            assert confirmed.json()["cart"]["total"] == 10900
        before = client.get("/api/cart").json()
        reset = client.post("/api/chat", json={"action": "reset"})
        assert reset.status_code == 200 and reset.json()["proposal"] is None
        after = client.get("/api/cart").json()
        assert after["items"] == before["items"]
        assert after["count"] == 3 and after["total"] == 10900
        assert after["kind"] == "demo"
        assert client.get(after["url"]).status_code == 200
