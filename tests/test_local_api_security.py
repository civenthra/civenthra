import json
import http.client
import threading
from contextlib import contextmanager
from http.server import ThreadingHTTPServer

import pytest

from civenthra import serve
from civenthra.local_api import (
    host_header_is_local, is_loopback_host, loopback_http_base_url,
    origin_matches, token_matches,
)


class _FakeIndex:
    n_chunks = 2
    dense = None

    @staticmethod
    def books():
        return [1]


class _FakeKnowledgeBase:
    index = _FakeIndex()
    graph = None
    graph_mode = "off"

    @staticmethod
    def stream_ask(question, mode, language):
        yield {"type": "sources", "passages": [{
            "title": '<img src=x onerror="alert(1)">',
            "author": "attacker", "chapter": "chapter", "score": 1,
            "text": '<script>alert("x")</script>',
        }]}
        yield {"type": "done"}


@contextmanager
def running_server(monkeypatch):
    serve.Handler.kb = _FakeKnowledgeBase()
    serve.Handler.session_token = "test-session-token"
    serve.Handler.csp_nonce = "test-csp-nonce"
    monkeypatch.setattr(serve.C, "VOICE_DIAGNOSTICS", False)
    server = ThreadingHTTPServer(("127.0.0.1", 0), serve.Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def fetch(server, path, *, token=None, origin=None, method="GET", body=None,
          content_type=None, host=None):
    headers = {}
    if token:
        headers["X-Civenthra-Token"] = token
    if origin:
        headers["Origin"] = origin
    if content_type:
        headers["Content-Type"] = content_type
    if host:
        headers["Host"] = host
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
    try:
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        return response.status, response.headers, response.read()
    finally:
        connection.close()


def test_loopback_helpers_are_strict():
    assert is_loopback_host("127.0.0.1")
    assert is_loopback_host("::1")
    assert not is_loopback_host("0.0.0.0")
    assert not is_loopback_host("example.com")
    assert host_header_is_local("127.0.0.1:8765", 8765)
    assert not host_header_is_local("127.0.0.1:9999", 8765)
    assert origin_matches("http://127.0.0.1:8765", "127.0.0.1:8765")
    assert not origin_matches("https://evil.example", "127.0.0.1:8765")
    assert token_matches("same", "same")
    assert not token_matches("wrong", "same")


@pytest.mark.parametrize("url", [
    "https://127.0.0.1:8791",
    "http://example.com:8791",
    "http://127.0.0.1",
    "http://user@127.0.0.1:8791",
    "http://127.0.0.1:8791/path",
    "http://127.0.0.1:8791?redirect=https://example.com",
    "http://127.attacker.example:8791",
])
def test_sidecar_url_rejects_nonlocal_or_ambiguous_targets(url):
    with pytest.raises(ValueError):
        loopback_http_base_url(url)


def test_sidecar_url_accepts_explicit_loopback_ports():
    assert loopback_http_base_url("http://127.0.0.1:8791/") == "http://127.0.0.1:8791"
    assert loopback_http_base_url("http://[::1]:8791") == "http://[::1]:8791"


def test_html_has_nonce_csp_and_no_dynamic_inner_html(monkeypatch):
    with running_server(monkeypatch) as server:
        status, headers, body = fetch(server, "/")

    text = body.decode("utf-8")
    assert status == 200
    assert "test-session-token" in text
    assert '<script nonce="test-csp-nonce">' in text
    assert "script-src 'nonce-test-csp-nonce'" in headers["Content-Security-Policy"]
    assert "innerHTML" not in text
    assert "paragraph.textContent" in text


def test_api_requires_token_and_rejects_cross_origin(monkeypatch):
    with running_server(monkeypatch) as server:
        assert fetch(server, "/api/info")[0] == 403
        assert fetch(server, "/api/info", token="test-session-token",
                     origin="https://evil.example")[0] == 403
        status, _, body = fetch(server, "/api/info", token="test-session-token")

    assert status == 200
    assert json.loads(body)["chunks"] == 2


def test_api_rejects_host_spoof_and_oversized_body(monkeypatch):
    with running_server(monkeypatch) as server:
        assert fetch(server, "/api/info", token="test-session-token",
                     host="evil.example")[0] == 403
        status, _, _ = fetch(
            server, "/api/ask", token="test-session-token", method="POST",
            body=b"x" * (serve.MAX_JSON_BODY + 1), content_type="application/json",
        )

    assert status == 413


def test_voice_diagnostics_are_disabled_by_default(monkeypatch):
    with running_server(monkeypatch) as server:
        status, _, _ = fetch(
            server, "/api/voice/report", token="test-session-token", method="POST",
            body=b"{}", content_type="application/json",
        )
    assert status == 404


def test_malicious_source_metadata_is_only_serialized_as_json(monkeypatch):
    payload = json.dumps({"question": "q", "mode": "civenthra", "language": "en"}).encode()
    with running_server(monkeypatch) as server:
        status, headers, body = fetch(
            server, "/api/ask", token="test-session-token", method="POST",
            body=payload, content_type="application/json",
        )
    assert status == 200
    assert headers.get_content_type() == "text/event-stream"
    assert b"<img src=x" in body
    assert "innerHTML" not in serve.PAGE
