# -*- coding: utf-8 -*-
"""llm · 本地推理后端（llama.cpp 的 llama-server）。

只做三件事：把 server 拉起来、说话、关掉。客户端只连接 127.0.0.1；整机网络隔离
仍由操作系统策略、隔离网络或物理断网负责。

用本地 server 而不是直接调 llama-cli，是因为 server 有 KV cache：多轮追问时前缀
命中缓存，第二轮开始基本瞬时。这对「对话」体验是决定性的。
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
from typing import Dict, Iterable, List, Optional

from . import config as C
from .local_api import free_loopback_port, loopback_port_is_open


def _http(url: str, payload=None, timeout: float = 300.0, stream: bool = False):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST" if data is not None else "GET",
    )
    return urllib.request.urlopen(req, timeout=timeout)


class LlamaServer:
    """llama-server 的进程壳 + HTTP 客户端。"""

    def __init__(self, model: Optional[str] = None, port: int = None,
                 ngl: int = None, ctx: int = None, threads: int = None):
        self.model = Path(model or C.MODEL_MAIN)
        configured_port = C.PORT if port is None else port
        self.port = int(configured_port) or free_loopback_port()
        self.ngl = int(C.NGL if ngl is None else ngl)
        self.ctx = int(ctx or C.CTX)
        self.threads = int(threads or C.THREADS)
        self.proc: Optional[subprocess.Popen] = None
        self._we_started = False

    # ── 生命周期 ────────────────────────────────────────────────────────────
    @property
    def base(self) -> str:
        return "http://127.0.0.1:%d" % self.port

    def is_up(self) -> bool:
        try:
            with _http(self.base + "/health", timeout=3.0) as r:
                return json.loads(r.read().decode("utf-8", "ignore")).get("status") == "ok"
        except Exception:
            return False

    def start(self, wait: float = 180.0) -> bool:
        if loopback_port_is_open(self.port):
            raise RuntimeError(
                "拒绝复用端口 %d 上身份不明的服务；请关闭占用进程或更换端口" % self.port
            )
        if not Path(C.LLAMA_SERVER).exists():
            raise FileNotFoundError("找不到 llama-server：%s" % C.LLAMA_SERVER)
        if not self.model.exists():
            raise FileNotFoundError("找不到模型文件：%s" % self.model)

        cmd = [
            str(C.LLAMA_SERVER), "-m", str(self.model),
            "--host", "127.0.0.1", "--port", str(self.port),
            "-ngl", str(self.ngl), "-c", str(self.ctx),
            "-t", str(self.threads), "--jinja", "--no-webui",
            "--log-disable",
        ]
        # 减少子进程意外使用代理的机会；这不是完整的子进程网络沙箱。
        env = dict(os.environ)
        for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                  "http_proxy", "https_proxy", "all_proxy"):
            env.pop(k, None)
        env["NO_PROXY"] = "*"

        creation = 0x00000008 if os.name == "nt" else 0  # DETACHED_PROCESS
        self.proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, env=env, creationflags=creation,
        )
        self._we_started = True
        t0 = time.time()
        while time.time() - t0 < wait:
            if self.proc.poll() is not None:
                raise RuntimeError("llama-server 启动后立即退出（返回码 %s）" % self.proc.returncode)
            if self.is_up():
                return True
            time.sleep(0.6)
        raise TimeoutError("llama-server 在 %.0fs 内未就绪" % wait)

    def stop(self):
        if self.proc and self._we_started and self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=10)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        self.proc = None
        self._we_started = False

    # ── 推理 ────────────────────────────────────────────────────────────────
    def chat(self, messages: List[Dict[str, str]], max_tokens: int = 512,
             temperature: float = 0.6, top_p: float = 0.9,
             stop: Optional[Iterable[str]] = None) -> str:
        payload = {
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "top_p": top_p,
            # Qwen3 默认会先输出思考过程；问答场景关掉，省 token 也更快
            "chat_template_kwargs": {"enable_thinking": False},
        }
        if stop:
            payload["stop"] = list(stop)
        with _http(self.base + "/v1/chat/completions", payload, timeout=600.0) as r:
            d = json.loads(r.read().decode("utf-8", "ignore"))
        return (d["choices"][0]["message"]["content"] or "").strip()

    def stream(self, messages: List[Dict[str, str]], max_tokens: int = 512,
               temperature: float = 0.6, top_p: float = 0.9):
        """逐字吐出（SSE）。给网页端做打字机效果。"""
        payload = {
            "messages": messages, "max_tokens": max_tokens,
            "temperature": temperature, "top_p": top_p, "stream": True,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        with _http(self.base + "/v1/chat/completions", payload, timeout=600.0) as r:
            for raw in r:
                line = raw.decode("utf-8", "ignore").strip()
                if not line.startswith("data:"):
                    continue
                body = line[5:].strip()
                if body == "[DONE]":
                    return
                try:
                    d = json.loads(body)
                except Exception:
                    continue
                delta = (d.get("choices") or [{}])[0].get("delta") or {}
                piece = delta.get("content")
                if piece:
                    yield piece

    def info(self) -> dict:
        try:
            with _http(self.base + "/props", timeout=5.0) as r:
                return json.loads(r.read().decode("utf-8", "ignore"))
        except Exception:
            return {}


_default: Optional[LlamaServer] = None
_lock = threading.Lock()


def default_server() -> LlamaServer:
    """进程级单例：CLI 和网页端共用同一个 server。"""
    global _default
    with _lock:
        if _default is None:
            _default = LlamaServer()
        return _default
