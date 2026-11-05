# -*- coding: utf-8 -*-
"""voice · 主进程这一侧的语音客户端。

文明穹顶主程序（Python 3.13）自己**不**能跑 faster-whisper —— 那个包只装在本机的
Python 3.12 里。所以语音能力被拆成一个独立进程（voicebox.py，绑 127.0.0.1:8790），
这个模块就是它的瘦客户端。

为什么走回环 HTTP 而不是 import：
  · 3.13 与 3.12 是两套 site-packages，带 C 扩展的包搬不过去；
  · 独立进程还能在崩溃/重载时互不影响，显存也能按需卸载。

主进程与 sidecar 只通过回环地址通信。进程出站限制不覆盖子进程，因此 sidecar 在
CIVENTHRA_PROCESS_NETWORK_GUARD=1 时会禁用 edge-tts；严格隔离仍需系统级措施。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional, Tuple

from . import config as C
from .local_api import TOKEN_ENV, TOKEN_HEADER

__all__ = ["VoiceClient", "client", "available"]

_TIMEOUT_INFO = 4
_TIMEOUT_ASR = 300        # 首次要载模型（约 4s），留足
_TIMEOUT_TTS = 60


def _base_url(port: int = None) -> str:
    return "http://127.0.0.1:%d" % int(port or C.VOICE_PORT)


class VoiceClient:
    """语音 sidecar 的客户端。所有方法都不抛异常，失败返回 None/False —— 语音是锦上添花，
    它挂了不能把提问这件事一起带走。"""

    def __init__(self, port: int = None, autostart: bool = None):
        self.port = int(port or C.VOICE_PORT)
        self.autostart = C.VOICE_AUTOSTART if autostart is None else autostart
        self._proc: Optional[subprocess.Popen] = None
        self._spawn_lock = threading.Lock()
        self._last_info: Optional[dict] = None
        self._last_info_at = 0.0
        self._info_ttl = 10.0

    # ── 底层 HTTP ──────────────────────────────────────────────────────────
    def _request(self, path: str, data: bytes = None, headers: dict = None,
                 timeout: int = 20) -> Optional[urllib.request.addinfourl]:
        request_headers = dict(headers or {})
        token = os.environ.get(TOKEN_ENV, "")
        if token:
            request_headers[TOKEN_HEADER] = token
        req = urllib.request.Request(_base_url(self.port) + path, data=data,
                                     headers=request_headers,
                                     method="POST" if data is not None else "GET")
        return urllib.request.urlopen(req, timeout=timeout)   # noqa: S310 —— 地址写死回环

    def ping(self) -> bool:
        try:
            with self._request("/health", timeout=_TIMEOUT_INFO) as r:
                payload = json.loads(r.read().decode("utf-8"))
                return (r.status == 200 and payload.get("service") == "civenthra-voicebox"
                        and payload.get("protocol") == 1)
        except Exception:
            return False

    # ── 拉起 sidecar ──────────────────────────────────────────────────────
    def ensure(self, wait_s: float = 25.0) -> bool:
        """sidecar 不在就拉起来。已经在跑则直接返回 True。"""
        if self.ping():
            return True
        if not self.autostart:
            return False
        with self._spawn_lock:
            if self.ping():                       # 双检：别人可能刚拉起来
                return True
            script = Path(C.VOICE_SCRIPT)
            if not script.exists():
                print("  [voice] 找不到 %s" % script, flush=True)
                return False
            py = str(C.VOICE_PYTHON)
            if py != "python" and not Path(py).exists():
                print("  [voice] 配置的解释器不存在：%s" % py, flush=True)
                return False
            env = dict(**__import__("os").environ)
            env.update({
                "PYTHONUTF8": "1",
                "VOICE_PORT": str(self.port),
                "VOICE_ASR_MODEL": C.VOICE_ASR_MODEL,
                "VOICE_ASR_DEVICE": C.VOICE_ASR_DEVICE,
                "VOICE_ASR_COMPUTE": C.VOICE_ASR_COMPUTE,
                "VOICE_SAPI_VOICE": C.VOICE_TTS_VOICE or "Huihui",
                "XIAOYA_REF_WAV": str(C.XIAOYA_REF_WAV),
                "CIVENTHRA_AUTHORIZATION_FILE": str(C.AUTHORIZATION_FILE),
                "CIVENTHRA_DEPLOYMENT_TERRITORY": C.DEPLOYMENT_TERRITORY,
            })
            if not env.get(TOKEN_ENV):
                print("  [voice] 缺少本次运行的本地 API 令牌，拒绝启动 sidecar", flush=True)
                return False
            if env.get("CIVENTHRA_PROCESS_NETWORK_GUARD") == "1":
                env.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"})
            flags = 0
            if sys.platform == "win32":
                flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            log = Path(C.ROOT) / "voicebox.log"
            print("  [voice] 拉起语音服务：%s voicebox.py --port %d" % (py, self.port),
                  flush=True)
            try:
                f = open(log, "a", encoding="utf-8", errors="replace")
                try:
                    self._proc = subprocess.Popen(
                        [py, "-X", "utf8", "-u", str(script), "--port", str(self.port)],
                        cwd=str(C.ROOT), env=env, stdout=f, stderr=subprocess.STDOUT,
                        stdin=subprocess.DEVNULL, creationflags=flags)
                finally:
                    f.close()
            except Exception as e:                 # noqa: BLE001
                print("  [voice] 拉起失败：%s" % str(e)[:160], flush=True)
                return False
            t0 = time.time()
            while time.time() - t0 < wait_s:
                if self.ping():
                    print("  [voice] 就绪（%.1fs）" % (time.time() - t0), flush=True)
                    self._last_info = None
                    return True
                if self._proc.poll() is not None:
                    print("  [voice] 进程退出 rc=%s，见 voicebox.log" % self._proc.returncode,
                          flush=True)
                    return False
                time.sleep(0.4)
            print("  [voice] 等待就绪超时（%.0fs）" % wait_s, flush=True)
            return False

    # ── 能力查询 ──────────────────────────────────────────────────────────
    def info(self, refresh: bool = False) -> Optional[dict]:
        now = time.time()
        if (not refresh and self._last_info is not None
                and now - self._last_info_at < self._info_ttl):
            return self._last_info
        try:
            with self._request("/health", timeout=_TIMEOUT_INFO) as r:
                d = json.loads(r.read().decode("utf-8"))
            if d.get("service") != "civenthra-voicebox" or d.get("protocol") != 1:
                raise ValueError("unexpected voice service identity")
            d["url"] = _base_url(self.port)
            self._last_info, self._last_info_at = d, now
            return d
        except Exception:
            self._last_info, self._last_info_at = None, now
            return None

    # ── ASR ───────────────────────────────────────────────────────────────
    def asr(self, pcm: bytes, sample_rate: int = 16000) -> Optional[dict]:
        """pcm = int16 小端单声道。返回 {"text":...} 或 None。"""
        if not pcm:
            return {"text": ""}
        if not self.ping() and not self.ensure():
            return None
        try:
            with self._request("/asr", data=pcm,
                               headers={"Content-Type": "application/octet-stream",
                                        "X-Sample-Rate": str(int(sample_rate))},
                               timeout=_TIMEOUT_ASR) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:                     # noqa: BLE001
            print("  [voice] ASR 失败：%s" % str(e)[:160], flush=True)
            return None

    # ── TTS ───────────────────────────────────────────────────────────────
    def tts(self, text: str, engine: str = None, voice: str = None,
            rate: int = None) -> Optional[Tuple[bytes, str, str]]:
        """返回 (音频字节, MIME, 实际引擎)。"""
        text = (text or "").strip()
        if not text:
            return None
        if not self.ping() and not self.ensure():
            return None
        body = json.dumps({
            "text": text,
            "engine": engine or C.VOICE_TTS_ENGINE,
            "voice": (voice if voice is not None else C.VOICE_TTS_VOICE) or None,
            "rate": C.VOICE_TTS_RATE if rate is None else rate,
        }, ensure_ascii=False).encode("utf-8")
        try:
            with self._request("/tts", data=body,
                               headers={"Content-Type": "application/json"},
                               timeout=_TIMEOUT_TTS) as r:
                return r.read(), r.headers.get("Content-Type", "audio/wav"), \
                    r.headers.get("X-Voice-Engine", "sapi")
        except urllib.error.HTTPError as e:
            print("  [voice] TTS HTTP %s：%s" % (e.code, e.read()[:160]), flush=True)
            return None
        except Exception as e:                     # noqa: BLE001
            print("  [voice] TTS 失败：%s" % str(e)[:160], flush=True)
            return None

    def unload(self) -> bool:
        try:
            with self._request("/unload", data=b"{}",
                               headers={"Content-Type": "application/json"},
                               timeout=10) as r:
                return r.status == 200
        except Exception:
            return False


_client: Optional[VoiceClient] = None


def client() -> VoiceClient:
    global _client
    if _client is None:
        _client = VoiceClient()
    return _client


def available() -> bool:
    return client().ping()
