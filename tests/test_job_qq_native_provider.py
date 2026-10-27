"""Thin Action mapping over the existing QQ HTTP/plugin/OneBot path."""
import asyncio, json, socket, threading, time
import httpx, pytest, uvicorn
from packages.actions.qq_channel_provider import QQChannelProvider
KEY={"runId":"run_fixture","stepId":"step_fixture","targetId":"123456","operationOrdinal":0}
@pytest.fixture
def qq_http(tmp_path, monkeypatch):
    import nonebot
    try: driver=nonebot.get_driver()
    except ValueError: nonebot.init(driver="~fastapi"); driver=nonebot.get_driver()
    from packages.qq import channels, plugin
    from packages.qq.channels.base import ChannelConfig
    from packages.qq.channels.onebot import OneBotChannel
    from packages.web import server as web
    calls=[]; mode={"requests":[]}
    class Gateway:
        async def call_api(self,api,**kwargs): calls.append((api,kwargs)); return {"message_id":654321}
    channel=OneBotChannel(ChannelConfig(name="onebot",bot_uin="999999"),bot_fallback=lambda:Gateway())
    monkeypatch.setattr(plugin,"_channel",channel); monkeypatch.setattr(channels,"_ACTIVE",channel); monkeypatch.setattr(channels,"_ACTIVE_CHANNELS",{"onebot":channel}); monkeypatch.setattr(plugin,"_HISTORY_DIR",tmp_path/"history")
    async def app(scope,receive,send):
        body=bytearray()
        async def recv():
            message=await receive()
            if message["type"]=="http.request": body.extend(message.get("body",b""))
            return message
        await driver.server_app(scope,recv,send)
        if body: mode["requests"].append(json.loads(body))
    sock=socket.socket(); sock.bind(("127.0.0.1",0)); sock.listen(32)
    server=uvicorn.Server(uvicorn.Config(app,lifespan="off",log_level="critical")); thread=threading.Thread(target=server.run,kwargs={"sockets":[sock]},daemon=True); thread.start()
    deadline=time.monotonic()+5
    while not server.started and thread.is_alive() and time.monotonic()<deadline: time.sleep(.01)
    assert server.started
    endpoint=f"http://127.0.0.1:{sock.getsockname()[1]}"
    monkeypatch.setenv("PAN_QQ_API_URL", endpoint)
    monkeypatch.setattr(web,"_qq_plugin_client", None)
    yield QQChannelProvider(web._send_scheduled_qq),calls,mode,channel
    web._qq_plugin_client = None
    server.should_exit=True; thread.join(5); sock.close()
def run(coro): return asyncio.run(coro)
def test_existing_chain_maps_action_and_preserves_raw_success(qq_http):
    provider,calls,mode,_=qq_http; receipt=run(provider.send("user","123456","hello",effect_key=KEY))
    assert receipt=={"ok":True,"message_id":654321}; assert calls==[("send_private_msg",{"user_id":123456,"message":"hello"})]; assert mode["requests"]==[{"target_type":"private","target_id":"123456","text":"hello"}]
def test_optional_bot_and_group_are_forwarded_to_existing_route(qq_http):
    provider,calls,mode,_=qq_http; receipt=run(provider.send("group","123456","hello",bot_uin="999999",effect_key=KEY))
    assert receipt=={"ok":True,"message_id":654321}; assert calls==[("send_group_msg",{"group_id":123456,"message":"hello"})]; assert mode["requests"]==[{"target_type":"group","target_id":"123456","text":"hello","bot_uin":"999999"}]
def test_existing_plugin_rejection_is_returned_unchanged(qq_http,monkeypatch):
    provider,calls,_,channel=qq_http; monkeypatch.setattr(channel,"_bot_fallback",None); receipt=run(provider.send("user","123456","hello",effect_key=KEY)); assert receipt["ok"] is False and receipt["error"]["code"]=="bot_not_connected" and calls==[]
@pytest.mark.parametrize("target_type,target_id,text",[("private","123456","hello"),("user","bad","hello"),("user","123456","")])
def test_action_validation_happens_before_transport(qq_http,target_type,target_id,text):
    provider,calls,_,_=qq_http
    with pytest.raises(ValueError): run(provider.send(target_type,target_id,text,effect_key=KEY))
    assert calls==[]
def test_unknown_original_failure_is_not_reclassified_as_success():
    async def sender(**_kwargs): return {"ok":False,"error":{"code":"connection_error","message":"lost"}}
    assert run(QQChannelProvider(sender).send("user","123456","hello",effect_key=KEY))=={"ok":False,"error":{"code":"connection_error","message":"lost"}}
