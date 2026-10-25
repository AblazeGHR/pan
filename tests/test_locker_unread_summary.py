import sqlite3
from fastapi import FastAPI
from fastapi.testclient import TestClient
from packages.interactions import api
from packages.interactions.store import InteractionStore
from packages.interactions.user_cards import UserCardStore


def make_store(tmp_path):
    store = InteractionStore(tmp_path / 'cards.sqlite')
    UserCardStore(store)
    with store.db() as db:
        db.execute("INSERT INTO cards(id,owner,recipient,request_key,signature,definition,created_at,delivery,is_read) VALUES(?,?,?,?,?,?,?,?,?)",
                   ('card-unread','owner','user','r1','sig','{}','now','{}',0))
        db.execute("INSERT INTO cards(id,owner,recipient,request_key,signature,definition,created_at,delivery,is_read) VALUES(?,?,?,?,?,?,?,?,?)",
                   ('card-read','owner','user','r2','sig','{}','now','{}',1))
        db.execute("INSERT INTO locker_messages(id,owner,recipient,signature,message,created_at,is_read) VALUES(?,?,?,?,?,?,?)",
                   ('message-unread','owner','user','sig','{}','now',0))
        db.execute("INSERT INTO locker_messages(id,owner,recipient,signature,message,created_at,is_read) VALUES(?,?,?,?,?,?,?)",
                   ('message-read','owner','user','sig','{}','now',1))
        db.execute("INSERT INTO user_cards(id,owner,recipient,signature,kind,body,attachments,created_at,answered_at,is_read) VALUES(?,?,?,?,?,?,?,?,?,?)",
                   ('reminding-answered','owner','user','sig','question','answer','[]','now','answered',0))
        db.execute("INSERT INTO user_cards(id,owner,recipient,signature,kind,body,attachments,created_at,answered_at,is_read) VALUES(?,?,?,?,?,?,?,?,?,?)",
                   ('reminding-pending','owner','user','sig2','question','pending','[]','now',None,0))
    return store


def test_summary_is_public_integer_only_and_unread_only(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    monkeypatch.setattr(api, 'get_store', lambda: store)
    app = FastAPI()
    api.register(app)
    response = TestClient(app).get('/api/locker-unread-summary')
    assert response.status_code == 200
    assert response.json() == {'unread': 3}
    assert set(response.json()) == {'unread'}


def test_summary_does_not_require_bearer(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    monkeypatch.setattr(api, 'get_store', lambda: store)
    app = FastAPI()
    api.register(app)
    assert TestClient(app).get('/api/locker-unread-summary').status_code == 200
