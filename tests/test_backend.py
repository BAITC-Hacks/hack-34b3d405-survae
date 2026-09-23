import time

import pytest
from fastapi.testclient import TestClient

from app import main
from app.catalog import Catalog, normalize_product


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("ALLOW_LOCAL_SETUP", "0")
    catalog = Catalog("demo")
    monkeypatch.setattr(main, "catalog", catalog)
    monkeypatch.setattr(main, "sessions", {})
    with TestClient(main.app) as c:
        state = c.get("/api/cart").json()
        c.headers["X-CSRF-Token"] = state["csrf"]
        yield c


def send(c, message):
    r = c.post("/api/chat", json={"message":message})
    assert r.status_code == 200, r.text
    return r.json()


def propose(c, items=None):
    r = c.post("/api/chat", json={"action":"propose", "items":items or [{"id":"DEMO-C16-A","quantity":2}]})
    assert r.status_code == 200, r.text
    assert r.json()["stage"] == "proposal"
    return r.json()["proposal"]["id"]


def accept(c, proposal_id):
    return c.post("/api/cart/confirm", json={"proposal_id":proposal_id,"confirmed":True})


def test_three_endpoint_checkout_and_replay(client):
    proposal_id = propose(client)
    assert client.get("/api/cart").json()["count"] == 0
    r = accept(client,proposal_id)
    assert r.status_code == 200
    assert r.json()["stage"] == "cart_updated"
    assert r.json()["cart"]["count"] == 2
    assert r.json()["cart"]["total"] == 2780
    assert r.json()["cart"]["url"] == "/cart"
    assert client.get("/cart").status_code == 200
    assert accept(client,proposal_id).json()["cart"]["count"] == 2


def test_old_frontend_action_id_compatible(client):
    proposal_id=propose(client)
    r=client.post("/api/cart/confirm",json={"action_id":proposal_id,"confirmed":True})
    assert r.status_code==200


@pytest.mark.parametrize("confirmation", [False, "true", 1, None])
def test_explicit_boolean_required(client, confirmation):
    pid=propose(client)
    r=client.post("/api/cart/confirm",json={"proposal_id":pid,"confirmed":confirmation})
    assert r.status_code in {400,422}
    assert client.get("/api/cart").json()["count"]==0


def test_chat_yes_cannot_mutate_cart(client):
    pid=propose(client)
    for message in ["да", "да, добавь", "подтверждаю"]:
        data=send(client,message)
        assert data["cart"]["count"]==0
        assert data["proposal"]["id"]==pid


def test_cancel_and_new_proposal_invalidate_previous(client):
    pid=propose(client)
    assert client.post("/api/chat",json={"action":"cancel"}).status_code==200
    assert accept(client,pid).status_code==409
    previous=propose(client)
    latest=propose(client)
    assert accept(client,previous).status_code==409
    assert accept(client,latest).status_code==200


@pytest.mark.parametrize("quantity", [0,-1,1000001,"abc"])
def test_invalid_quantity(client, quantity):
    r=client.post("/api/chat",json={"action":"propose","items":[{"id":"DEMO-C16-A","quantity":quantity}]})
    assert r.status_code==422


def test_stock_and_minimum(client):
    for product,quantity in [("DEMO-C16-A",9),("DEMO-CABLE-A",11),("DEMO-C16-Z",1)]:
        r=client.post("/api/chat",json={"action":"propose","items":[{"id":product,"quantity":quantity}]})
        assert r.status_code==409
    assert client.get("/api/cart").json()["count"]==0


def test_existing_cart_counts_against_stock(client):
    pid=propose(client,[{"id":"DEMO-C16-A","quantity":6}])
    assert accept(client,pid).status_code==200
    r=client.post("/api/chat",json={"action":"propose","items":[{"id":"DEMO-C16-A","quantity":3}]})
    assert r.status_code==409


@pytest.mark.parametrize("change", ["price","stock","unavailable","attributes"])
def test_fresh_validation_blocks_entire_order(client, monkeypatch, change):
    pid=propose(client,[{"id":"DEMO-C16-A","quantity":2},{"id":"DEMO-LED-B","quantity":1}])
    item=main.catalog.items["DEMO-LED-B"]
    if change=="price": item["price"]+=100
    elif change=="stock": item["stores"][0]["quantity"]=0
    elif change=="attributes": item["attributes"]["Номинальное напряжение"]="12 В"
    else: main.catalog.items.pop("DEMO-LED-B")
    assert accept(client,pid).status_code==409
    assert client.get("/api/cart").json()["count"]==0


def test_expiry(client):
    pid=propose(client)
    session=main.sessions[client.cookies.get("survae_session")]
    session["pending"]["expires_at"]=time.time()-1
    assert accept(client,pid).status_code==409


def test_session_isolation(client):
    pid=propose(client)
    old_session=client.cookies.get("survae_session")
    client.cookies.clear()
    state=client.get("/api/cart").json()
    client.headers["X-CSRF-Token"]=state["csrf"]
    assert accept(client,pid).status_code==409
    assert state["count"]==0
    client.cookies={"survae_session":old_session}
    client.headers["X-CSRF-Token"]=client.get("/api/cart").json()["csrf"]
    assert accept(client,pid).status_code==200


def test_csrf_and_origin(client):
    r=client.post("/api/chat",json={"message":"привет"},headers={"X-CSRF-Token":"invalid"})
    assert r.status_code==403
    r=client.post("/api/chat",json={"message":"привет"},headers={"Origin":"https://untrusted.invalid"})
    assert r.status_code==403
    r=client.options("/api/chat",headers={"Origin":"http://localhost:5173",
        "Access-Control-Request-Method":"POST","Access-Control-Request-Headers":"content-type,x-csrf-token"})
    assert r.status_code==200
    assert r.headers["access-control-allow-origin"]=="http://localhost:5173"
    r=client.post("/api/chat",json={"message":"привет"},headers={"Origin":"http://localhost:5173"})
    assert r.status_code==200


def test_empty_chat_validation(client):
    assert client.post("/api/chat",json={"message":"   "}).status_code==422


def test_catalog_article_and_certificate(client):
    data=send(client,"Есть DEMO-C16-A?")
    assert len(data["products"])==1
    assert data["products"][0]["available"]==8
    assert data["products"][0]["certificates"]


def test_out_of_stock_analogue(client):
    data=send(client,"Есть DEMO-C16-Z?")
    assert data["alternatives"]
    assert all(p["reason"] and p["available"]>0 for p in data["alternatives"])


def test_minimum_from_mentioned_article(client):
    data=send(client,"Минимальная партия DEMO-CABLE-A?")
    assert "10" in data["message"]


def test_guided_kit_full_flow(client):
    data=send(client,"Хочу подсветку кухни")
    assert data["stage"]=="clarification"
    assert "2 м" in data["options"]
    assert send(client,"2 м")["kit"]["answers"]["length_m"]==2
    send(client,"Есть розетка рядом")
    send(client,"Сухое место, вдали от воды")
    data=send(client,"Тёплый свет")
    assert data["stage"]=="proposal"
    assert data["kit"]["complete"] is True
    assert len(data["kit"]["checks"])==4
    assert len(data["proposal"]["items"])==2
    assert data["proposal"]["total"]==10900
    assert data["cart"]["count"]==0
    assert accept(client,data["proposal"]["id"]).json()["cart"]["count"]==3


def test_kit_no_outlet_requires_clarification(client):
    send(client,"Подсветка кухни 2 м")
    data=send(client,"нет")
    assert data["kit"]["answers"]["outlet"] is False
    assert data["proposal"] is None
    assert "электрик" in data["message"]


def test_kit_unknown_does_not_invent_answer(client):
    send(client,"Подсветка кухни")
    data=send(client,"Не знаю")
    assert data["kit"]["answers"]["length_m"] is None
    assert data["proposal"] is None


def test_kit_incompatible_connector_is_not_approved(client):
    main.catalog.items["DEMO-PSU-60"]["attributes"]["Разъём"]="OTHER"
    data=send(client,"Подсветка кухни 2 м, есть розетка рядом, сухое место, теплый свет")
    assert data["kit"]["complete"] is False
    assert data["proposal"] is None


def test_kit_unavailable_stock_no_proposal(client):
    main.catalog.items["DEMO-PSU-60"]["stores"][0]["quantity"]=0
    data=send(client,"Подсветка кухни 2 м, есть розетка рядом, сухое место, теплый свет")
    assert data["kit"]["complete"] is False
    assert data["proposal"] is None


def test_live_kit_never_uses_demo(client,monkeypatch):
    main.catalog.mode="live"
    async def empty_search(*args): return []
    monkeypatch.setattr(main.catalog,"search",empty_search)
    data=send(client,"Подсветка кухни 2 м, есть розетка рядом, сухое место, теплый свет")
    assert data["catalog_mode"]=="live"
    assert data["kit"]["complete"] is False
    assert data["products"]==[]
    assert data["proposal"] is None


def test_reset_preserves_cart(client):
    assert accept(client,propose(client)).status_code==200
    send(client,"Подсветка кухни 2 м")
    data=client.post("/api/chat",json={"action":"reset"}).json()
    assert data["cart"]["count"]==2
    assert data["kit"] is None


def test_price_never_taken_from_frontend(client):
    r=client.post("/api/chat",json={"action":"propose","items":[{"id":"DEMO-C16-A","quantity":2,"price":1}]})
    assert r.json()["proposal"]["total"]==2780


def test_failed_new_proposal_invalidates_old(client):
    pid=propose(client)
    r=client.post("/api/chat",json={"action":"propose","items":[{"id":"missing","quantity":1}]})
    assert r.status_code==409
    assert accept(client,pid).status_code==409


@pytest.mark.parametrize("operation", ["remove", "clear"])
def test_cart_removal_also_requires_confirmation(client, operation):
    assert accept(client,propose(client)).status_code == 200
    data=client.post("/api/chat",json={"action":"propose","operation":operation,
        "items":[{"id":"DEMO-C16-A","quantity":1}]}).json()
    assert data["cart"]["count"]==2
    assert accept(client,data["proposal"]["id"]).json()["cart"]["count"]==0


def test_real_shape_normalization_flags_conflicting_current():
    p=normalize_product({"id":515291,"name":"АВ 160А", "article":"EXAMPLE", "price":100,
                         "quantity":23,"properties":{"NOMINALNYY_TOK":"250 А"}})
    assert p["warnings"]


def test_ai_slot_response_validation(client,monkeypatch):
    from app import ai
    import httpx
    monkeypatch.setenv("OPENAI_API_KEY","unit-test-not-a-real-key")
    calls=[]
    class FakeClient:
        def __init__(self,**kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def post(self,url,**kwargs):
            calls.append(kwargs["json"])
            return httpx.Response(200,request=httpx.Request("POST",url),json={"output":[{"content":[{
                "type":"output_text", "text":'{"length_m":2,"outlet":true,"dry":true,"color":"warm"}'}]}]})
    monkeypatch.setattr(ai.httpx,"AsyncClient",FakeClient)
    data=send(client,"Хочу подсветку кухни длиной два метра")
    assert data["engine"]=="openai"
    assert data["kit"]["complete"] is True
    assert calls[0]["store"] is False
    assert calls[0]["text"]["format"]["strict"] is True


def test_api_failure_is_labeled_fallback(client,monkeypatch):
    from app import ai
    import httpx
    monkeypatch.setenv("OPENAI_API_KEY","unit-test-not-a-real-key")
    class BrokenClient:
        def __init__(self,**kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def post(self,*args,**kwargs): raise httpx.ConnectError("test")
    monkeypatch.setattr(ai.httpx,"AsyncClient",BrokenClient)
    data=send(client,"Подсветка кухни")
    assert data["engine"]=="fallback"
    assert data["proposal"] is None
