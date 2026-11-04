from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import app.upstox_client as client


@pytest.fixture
def clock(monkeypatch):
    state = [0.0]
    origin = datetime(2026, 11, 2, 7, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(client.time, "monotonic", lambda: state[0])
    monkeypatch.setattr(client.time, "sleep", lambda seconds: state.__setitem__(0, state[0]+seconds))
    monkeypatch.setattr(client, "_utc_now", lambda: origin+timedelta(seconds=state[0]))
    monkeypatch.setattr(client, "get_settings", lambda: SimpleNamespace(upstox_access_token="dummy",
        request_timeout_seconds=10, upstox_total_deadline_seconds=2, upstox_max_retry_delay_seconds=.5))
    return state


@pytest.mark.parametrize("header", ["21600", "86400", "nonsense", "-1", "NaN"])
def test_long_or_malformed_retry_after_never_sleeps_or_retries(clock, monkeypatch, header):
    calls = []
    monkeypatch.setattr(client, "_bounded_get", lambda *a: calls.append(a) or {"status":429,"retry_after":header})
    with pytest.raises(client.UpstoxRequestError) as error: client.get_quote("instrument")
    assert error.value.category=="rate_limit" and len(calls)==1 and clock[0]==0


def test_total_deadline_counts_retries_and_prevents_late_attempt(clock, monkeypatch):
    calls=[]
    def get(*args):
        calls.append(args);clock[0]+=.9
        return {"status":503,"retry_after":"0"}
    monkeypatch.setattr(client,"_bounded_get",get)
    with pytest.raises(client.UpstoxRequestError): client.get_quote("instrument")
    assert len(calls)==3  # third response crosses the 2s deadline and is discarded
    assert calls[-1][-1]==pytest.approx(.2)


def test_short_retry_after_respects_attempt_cap(clock, monkeypatch):
    calls=[]
    monkeypatch.setattr(client,"_bounded_get",lambda *a: calls.append(a) or {"status":429,"retry_after":"0"})
    with pytest.raises(client.UpstoxRequestError): client.get_profile()
    assert len(calls)==3


def test_session_boundary_cancels_retry_before_next_get(clock, monkeypatch):
    calls=[]
    monkeypatch.setattr(client,"_bounded_get",lambda *a: calls.append(a) or {"status":503,"retry_after":None})
    with client.request_window(end=client._utc_now()+timedelta(seconds=.2),before_attempt=lambda: None):
        with pytest.raises(client.UpstoxRequestError): client.get_quote("instrument")
    assert len(calls)==1 and clock[0]==0


def test_revalidation_before_each_physical_attempt(clock, monkeypatch):
    calls=[];checks=[]
    monkeypatch.setattr(client,"_bounded_get",lambda *a: calls.append(a) or {"status":503})
    def check():
        checks.append(1)
        if len(checks)>1: raise ValueError("window revoked")
    with client.request_window(end=client._utc_now()+timedelta(seconds=10),before_attempt=check):
        with pytest.raises(client.UpstoxRequestError):client.get_profile()
    assert len(calls)==1 and len(checks)==2


def test_killable_worker_timeout_cleans_up_without_unbounded_join(monkeypatch):
    calls=[]
    class Pipe:
        def close(self):pass
        def poll(self, remaining):calls.append(("poll",remaining));return False
    class Worker:
        pid=1
        def start(self):calls.append("start")
        def is_alive(self):return True
        def terminate(self):calls.append("terminate")
        def kill(self):calls.append("kill")
        def join(self,timeout):calls.append(("join",timeout))
    class Timer:
        def __init__(self,delay,callback):calls.append(("timer",delay))
        def start(self):pass
        def cancel(self):pass
    monkeypatch.setattr(client.multiprocessing,"get_context",lambda kind: SimpleNamespace(
        Pipe=lambda **k:(Pipe(),Pipe()),Process=lambda **k:Worker()))
    monkeypatch.setattr(client.threading,"Timer",Timer)
    result=client._bounded_get("https://api.upstox.com/test",{},None,1,1)
    assert result=={"error":"network_error","retryable":False}
    assert "terminate" in calls and "kill" in calls
    assert calls.count(("join",.5))==2


def test_worker_is_get_only_no_redirects_no_implicit_retries(monkeypatch):
    output=[];calls=[]
    class Response:
        status_code=200;headers={}
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def iter_content(self,size):return [b'{"data":{"candles":[]}}']
    class Session:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def mount(self,*a):pass
        def get(self,*a,**kwargs):calls.append(kwargs);return Response()
    monkeypatch.setattr(client.requests,"Session",Session)
    client._http_worker(SimpleNamespace(send=output.append,close=lambda:None),"https://api.upstox.com/test",{},None,1)
    assert output[0]["status"]==200 and "data" in output[0]
    assert calls[0]["allow_redirects"] is False and calls[0]["stream"] is True
