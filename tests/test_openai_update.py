"""Offline regression checks for the teammate's OpenAI update."""
import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from app import ai, main
from app.catalog import Catalog


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.setenv('ALLOW_LOCAL_SETUP', '0')
    monkeypatch.setattr(main, 'catalog', Catalog('demo'))
    monkeypatch.setattr(main, 'sessions', {})
    with TestClient(main.app) as c:
        c.headers['X-CSRF-Token'] = c.get('/api/state').json()['csrf']
        yield c


def test_ai_clarification_keeps_history_and_empty_cart(client, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'test-placeholder')
    seen = []
    async def understand(message, history, products, attachment=None):
        seen.append(list(history))
        return {'intent':'clarify', 'question':'Где нужен свет?', 'query':'', 'topic':'general'}, 'openai'
    monkeypatch.setattr(main, 'understand', understand)
    for message in ['Не знаю, что купить', 'Да', 'Нет']:
        result = client.post('/api/chat', json={'message':message}).json()
        assert result['stage'] == 'clarification'
        assert result['message'] == 'Где нужен свет?'
        assert result['cart']['count'] == 0
    assert len(seen[1]) == 2


def test_ai_does_not_bypass_confirmation(client, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'test-placeholder')
    client.post('/api/chat', json={'action':'propose', 'items':[{'id':'DEMO-C16-A','quantity':1}]})
    result = client.post('/api/chat', json={'message':'да'}).json()
    assert result['proposal'] is not None
    assert result['cart']['count'] == 0


def test_failed_key_verification_never_saves(client, monkeypatch, tmp_path):
    monkeypatch.setenv('ALLOW_LOCAL_SETUP', '1')
    monkeypatch.setenv('OPENAI_API_KEY', 'previous-placeholder')
    monkeypatch.setattr(main, 'ROOT', tmp_path)
    async def reject(key):
        raise ValueError('Ключ отклонён')
    monkeypatch.setattr(main, 'verify_key', reject)
    response = client.post('/api/setup', json={'api_key':'sk-' + 'fake' * 6})
    assert response.status_code == 400
    assert not (tmp_path / '.env').exists()
    assert main.os.environ['OPENAI_API_KEY'] == 'previous-placeholder'


def test_verified_key_saved_only_after_check(client, monkeypatch, tmp_path):
    monkeypatch.setenv('ALLOW_LOCAL_SETUP', '1')
    monkeypatch.setattr(main, 'ROOT', tmp_path)
    async def verify(key):
        assert not (tmp_path / '.env').exists()
    monkeypatch.setattr(main, 'verify_key', verify)
    response = client.post('/api/setup', json={'api_key':'sk-' + 'fake' * 6})
    assert response.status_code == 200
    assert (tmp_path / '.env').exists()


@pytest.mark.parametrize('status', [401, 429, 403, 500])
def test_verify_key_errors_are_sanitized(monkeypatch, status):
    class FakeClient:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, **kwargs):
            assert kwargs['json']['store'] is False
            return httpx.Response(status, json={'error':'private-provider-detail'}, request=httpx.Request('POST', url))
    monkeypatch.setattr(ai.httpx, 'AsyncClient', FakeClient)
    with pytest.raises(ValueError) as error:
        asyncio.run(ai.verify_key('test-placeholder'))
    assert 'private-provider-detail' not in str(error.value)
    assert 'test-placeholder' not in str(error.value)


@pytest.mark.parametrize('fail', [False, True])
def test_composed_reply_preserves_server_facts(client, monkeypatch, fail):
    async def understand(*args):
        return {'intent':'search', 'query':'DEMO-C16-A', 'topic':'general'}, 'openai'
    async def compose(message, history, facts):
        assert facts['result']['products'][0]['article'] == 'DEMO-C16-A'
        if fail: raise ValueError('provider failure')
        return 'Пояснение AI'
    monkeypatch.setattr(main, 'understand', understand)
    monkeypatch.setattr(main, 'compose_reply', compose)
    result = client.post('/api/chat', json={'message':'DEMO-C16-A'}).json()
    assert 'Нашёл подходящие позиции' in result['message']
    assert result['cart']['count'] == 0
    assert result['engine'] == ('fallback' if fail else 'openai')
