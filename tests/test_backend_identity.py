import json
import socket
from unittest.mock import patch

import pytest

from civenthra.embed import EmbedServer
from civenthra.llm import LlamaServer
from civenthra.voice import VoiceClient


class _Response:
    status = 200

    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


@pytest.fixture
def occupied_loopback_port():
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    try:
        yield listener.getsockname()[1]
    finally:
        listener.close()


def test_generation_backend_refuses_preexisting_service(occupied_loopback_port, tmp_path):
    server = LlamaServer(model=tmp_path / "model.gguf", port=occupied_loopback_port)
    with pytest.raises(RuntimeError, match="身份不明"):
        server.start(wait=0.01)


def test_embedding_backend_refuses_preexisting_service(occupied_loopback_port, tmp_path):
    server = EmbedServer(model=tmp_path / "embed.gguf", port=occupied_loopback_port)
    with pytest.raises(RuntimeError, match="身份不明"):
        server.start(wait=0.01)


def test_default_model_ports_are_randomized():
    assert LlamaServer().port > 0
    assert EmbedServer().port > 0


def test_voice_client_requires_service_identity():
    client = VoiceClient(autostart=False)
    with patch.object(client, "_request", return_value=_Response({"ok": True})):
        assert not client.ping()
    with patch.object(client, "_request", return_value=_Response({
            "ok": True, "service": "civenthra-voicebox", "protocol": 1})):
        assert client.ping()
