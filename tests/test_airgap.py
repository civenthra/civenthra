"""Tests for the process-local network guard. No real external request is made."""

import os
import socket

import pytest

from civenthra import airgap


@pytest.fixture(autouse=True)
def clean_guard():
    airgap.release()
    yield
    airgap.release()


def test_external_dns_and_tcp_are_rejected_without_real_network_access():
    airgap.engage()

    with pytest.raises(airgap.AirgapError):
        socket.getaddrinfo("civenthra.invalid", 443)
    with pytest.raises(airgap.AirgapError):
        socket.create_connection(("203.0.113.1", 9), timeout=0.01)
    assert airgap.verify()


def test_loopback_remains_allowed():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    airgap.engage()

    client = socket.create_connection(("127.0.0.1", server.getsockname()[1]), timeout=1)
    client.close()
    server.close()


def test_loopback_prefix_hostname_is_not_trusted():
    assert airgap._is_local("127.0.0.2")
    assert airgap._is_local("::1")
    assert not airgap._is_local("127.attacker.example")
    assert not airgap._is_local("127.0.0.1.attacker.example")

    airgap.engage()
    with pytest.raises(airgap.AirgapError):
        socket.getaddrinfo("127.attacker.example", 443)


def test_release_restores_functions_and_proxy_environment(monkeypatch):
    original_getaddrinfo = socket.getaddrinfo
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9999")
    monkeypatch.delenv("NO_PROXY", raising=False)

    airgap.engage()
    assert "HTTP_PROXY" not in os.environ
    assert os.environ["NO_PROXY"] == "*"
    assert os.environ["CIVENTHRA_PROCESS_NETWORK_GUARD"] == "1"

    airgap.release()
    assert socket.getaddrinfo is original_getaddrinfo
    assert os.environ["HTTP_PROXY"] == "http://127.0.0.1:9999"
    assert "NO_PROXY" not in os.environ
    assert "CIVENTHRA_PROCESS_NETWORK_GUARD" not in os.environ


def test_guard_context_is_nestable():
    with airgap.guard():
        assert airgap.engaged()
        with airgap.guard():
            assert airgap.engaged()
        assert airgap.engaged()
    assert not airgap.engaged()
