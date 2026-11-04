"""Offline test isolation: never read .env or start an HTTP worker with real credentials."""
import os

import dotenv
import pytest
import requests
import socket

dotenv.load_dotenv = lambda *args, **kwargs: False
os.environ["UPSTOX_ACCESS_TOKEN"] = ""
os.environ["DEEPSEEK_API_KEY"] = ""

_SESSION = requests.Session
_GET = requests.Session.get


@pytest.fixture(autouse=True)
def no_external_sockets(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("offline tests cannot open external sockets")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)


@pytest.fixture(autouse=True)
def mocked_upstox_worker_only(monkeypatch, request):
    if request.node.path.name == "test_upstox_deadlines.py":
        return  # This module explicitly mocks worker/process and clock boundaries.
    import app.upstox_client as client

    def fake_worker(url, headers, params, timeout, remaining):
        if requests.Session is _SESSION and requests.Session.get is _GET:
            raise AssertionError("HTTP tests must explicitly mock their transport")
        try:
            session = requests.Session()
            response = session.get(url, headers=headers, params=params, timeout=timeout)
            result = {"status": response.status_code, "retry_after": None}
            if 200 <= response.status_code < 300:
                try:
                    result["data"] = response.json()
                except ValueError:
                    result["error"] = "json_decode"
            return result
        except requests.RequestException:
            return {"error": "network_error", "retryable": False}

    monkeypatch.setattr(client, "_bounded_get", fake_worker)
