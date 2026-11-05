"""文明穹顶 · 小雅音色 TTS sidecar  @127.0.0.1:8791

为什么单独一个进程、还要单独一个 venv？
  文明穹顶主程序跑 managed Python 3.13、纯标准库；语音 sidecar 跑 Python 3.12 用户级环境。
  而 XTTS v2 需要的 coqui-tts 要 transformers<5，与用户级环境里的 4.44.2 系包会打架，
  所以单独建立隔离的 `xiaoya-tts` venv（具体位置由本地配置决定）
  （该 venv 用两个 .pth 复用系统级 + 用户级的 torch/soundfile/librosa，没重复装 torch）。

两个踩过的坑，别再重犯：
  1. coqui-tts 0.27 的缓存结构是 `TTS_HOME/tts/<model_dir>`，比老版多一层 `tts`。
     老缓存实际在 `...\\.cache\\asr\\tts\\tts_models--...`，所以 TTS_HOME 必须指到
     `...\\.cache\\asr`；指到 `...\\asr\\tts` 它会认为没缓存、重新下载 1.87GB。
  2. torchaudio 2.9 的 load/save 改走 torchcodec，而 torchcodec 要 FFmpeg 动态库。
     这里直接 monkey-patch 成 soundfile —— 为一个读写动作装整套 FFmpeg 不划算。

HTTP 层刻意用标准库 http.server：只要能传一段 wav，不值得为它引入 web 框架。
"""

from __future__ import annotations

import argparse
import io
import json
import math
import os
import sys
import threading
import time
import urllib.parse
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from civenthra.authorizations import require_authorization
from civenthra.local_api import (
    TOKEN_ENV, TOKEN_HEADER, host_header_is_local, is_loopback_host, token_matches,
)

HOST = os.environ.get("XIAOYA_HOST", "127.0.0.1")
PORT = int(os.environ.get("XIAOYA_PORT", "8791"))
LANG = os.environ.get("XIAOYA_LANG", "zh-cn")
IDLE_UNLOAD_S = float(os.environ.get("XIAOYA_IDLE_UNLOAD_S", "900"))
MAX_CHARS = int(os.environ.get("XIAOYA_MAX_CHARS", "600"))
MAX_JSON_BYTES = int(os.environ.get("XIAOYA_MAX_JSON_BYTES", str(64 * 1024)))
LOCAL_API_TOKEN = os.environ.get(TOKEN_ENV, "")

# 模型缓存根：注意是 ...\.cache\asr，不是 ...\.cache\asr\tts
TTS_HOME = os.environ.get("XIAOYA_TTS_HOME", str(Path.home() / ".cache" / "civenthra" / "tts"))
MODEL_NAME = "tts_models/multilingual/multi-dataset/xtts_v2"

# 小雅音色参考音频（17.48s~26.92s 切片，9.44s / 24kHz 单声道）
REF_WAV = os.environ.get(
    "XIAOYA_REF_WAV",
    str(Path(__file__).resolve().parent / "assets" / "voice" / "local-reference.wav"),
)
DEPLOYMENT_TERRITORY = os.environ.get("CIVENTHRA_DEPLOYMENT_TERRITORY", "")
os.environ["TTS_HOME"] = TTS_HOME


def _patch_torchaudio() -> None:
    """把 torchaudio 的 load/save 换成 soundfile 实现，绕开 torchcodec/FFmpeg。"""
    import numpy as np
    import soundfile as sf
    import torch
    import torchaudio

    def _load(uri, *a, **kw):
        data, sr = sf.read(str(uri), dtype="float32", always_2d=True)
        return torch.from_numpy(data.T.copy()), sr

    def _save(uri, src, sample_rate, *a, **kw):
        data = src.detach().cpu().numpy() if hasattr(src, "detach") else np.asarray(src)
        if data.ndim == 2:
            data = data.T
        sf.write(str(uri), data, int(sample_rate))

    torchaudio.load = _load
    torchaudio.save = _save


_patch_torchaudio()


class _Xtss:
    """懒加载 + 空闲卸载。显存要和 llama.cpp 抢，不能一直占着。"""

    def __init__(self) -> None:
        self._tts = None
        self._device = None
        self._lock = threading.Lock()
        self._last_use = 0.0
        self.load_seconds = None
        self._stop = threading.Event()

    def _load_locked(self) -> None:
        if self._tts is not None:
            return
        require_authorization(
            "xtts-v2-voice-cloning",
            "local-noncommercial-voice-cloning",
            DEPLOYMENT_TERRITORY,
            artifact=Path(REF_WAV),
            required_constraints=("non-commercial", "no-redistribution"),
        )
        from TTS.api import TTS

        t0 = time.perf_counter()
        tts = TTS(MODEL_NAME)
        try:
            tts = tts.to("cuda")
            self._device = "cuda"
        except Exception as exc:                      # 驱动不合时别让服务起不来
            print("cuda 不可用（%r），退回 CPU" % (exc,), flush=True)
            tts = tts.to("cpu")
            self._device = "cpu"
        self._tts = tts
        self.load_seconds = round(time.perf_counter() - t0, 1)
        print("XTTS 已加载 %.1fs device=%s" % (self.load_seconds, self._device), flush=True)

    def synth(self, text: str, language: str | None = None, speed: float = 1.0) -> bytes:
        text = (text or "").strip()
        if not text:
            raise ValueError("empty text")
        if len(text) > MAX_CHARS:
            text = text[:MAX_CHARS]
        if (not isinstance(speed, (int, float)) or not math.isfinite(float(speed))
                or not 0.5 <= float(speed) <= 1.6):
            raise ValueError("speed must be a finite value between 0.5 and 1.6")
        with self._lock:
            self._load_locked()
            t0 = time.perf_counter()
            wav_list = self._tts.tts(
                text=text,
                speaker_wav=REF_WAV,
                language=language or LANG,
                speed=float(speed),
            )
            self._last_use = time.time()
            ms = int((time.perf_counter() - t0) * 1000)
        import numpy as np

        sr = getattr(self._tts.synthesizer, "output_sample_rate", 24000)
        pcm = np.asarray(wav_list, dtype="float32")
        pcm = np.clip(pcm, -1.0, 1.0)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(int(sr))
            w.writeframes((pcm * 32767.0).astype("<i2").tobytes())
        print("合成 %d 字 / %d ms / %d bytes" % (len(text), ms, buf.tell()), flush=True)
        return buf.getvalue()

    def unload(self) -> bool:
        with self._lock:
            if self._tts is None:
                return False
            self._tts = None
            self.load_seconds = None
            try:
                import torch

                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass
            return True

    def info(self) -> dict:
        return {
            "ready": self._tts is not None,
            "device": self._device,
            "model": MODEL_NAME,
            "tts_home_configured": bool(TTS_HOME),
            "reference_configured": bool(REF_WAV),
            "ref_exists": Path(REF_WAV).exists(),
            "language": LANG,
            "load_seconds": self.load_seconds,
            "idle_unload_s": IDLE_UNLOAD_S,
        }

    def start_idle_reaper(self) -> None:
        if IDLE_UNLOAD_S <= 0:
            return

        def _loop():
            while not self._stop.wait(30):
                with self._lock:
                    idle = self._tts is not None and (time.time() - self._last_use) > IDLE_UNLOAD_S
                if idle:
                    self.unload()
                    print("空闲 %.0fs，已卸载 XTTS 释放显存" % IDLE_UNLOAD_S, flush=True)

        threading.Thread(target=_loop, daemon=True, name="xtts-reaper").start()


XT = _Xtss()


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "xiaoya-tts/1.0"
    sys_version = ""
    slots = threading.BoundedSemaphore(1)

    def log_message(self, fmt, *args):        # 默认日志太吵，收掉
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        remote = self.client_address[0] if self.client_address else None
        host = self.headers.get("Host")
        return (
            is_loopback_host(remote)
            and host_header_is_local(host, self.server.server_port)
            and not self.headers.get("Origin")
            and token_matches(self.headers.get(TOKEN_HEADER), LOCAL_API_TOKEN)
        )

    def do_GET(self):
        if not self._authorized():
            return self._json({"error": "forbidden"}, 403)
        path = urllib.parse.urlparse(self.path).path
        if path in ("/health", "/"):
            self._json({"ok": True, "service": "civenthra-xiaoya-tts",
                        "protocol": 1, **XT.info()})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        if not self._authorized():
            return self._json({"error": "forbidden"}, 403)
        path = urllib.parse.urlparse(self.path).path
        if self.headers.get("Transfer-Encoding"):
            return self._json({"error": "chunked body rejected"}, 400)
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._json({"error": "invalid content length"}, 400)
        if n < 0 or n > MAX_JSON_BYTES:
            self.close_connection = True
            return self._json({"error": "request body too large"}, 413)
        if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
            return self._json({"error": "unsupported content type"}, 415)
        raw = self.rfile.read(n) if n else b""
        if path == "/unload":
            self._json({"ok": True, "unloaded": XT.unload()})
            return
        if path != "/tts":
            self._json({"error": "not found"}, 404)
            return
        try:
            req = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeError, json.JSONDecodeError):
            self._json({"error": "bad json"}, 400)
            return
        text = req.get("text", "") if isinstance(req, dict) else ""
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_CHARS:
            return self._json({"error": "invalid text"}, 400)
        if not self.slots.acquire(blocking=False):
            return self._json({"error": "busy"}, 429)
        try:
            data = XT.synth(text, req.get("language"), req.get("speed", 1.0))
        except Exception as exc:
            print("xiaoya request failed: %s" % str(exc)[:300], file=sys.stderr, flush=True)
            self._json({"error": "synthesis failed"}, 500)
            return
        finally:
            self.slots.release()
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Voice-Engine", "xiaoya-xtts")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)


def main() -> int:
    global PORT, LOCAL_API_TOKEN
    ap = argparse.ArgumentParser(description="文明穹顶 · 小雅音色 TTS sidecar")
    ap.add_argument("--host", default=HOST)
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--preload", action="store_true", help="启动即加载模型")
    ap.add_argument("--once", metavar="TEXT", help="合成一句话就退出（排障用）")
    ap.add_argument("--out", default="xiaoya_out.wav")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--token", default=None, help=argparse.SUPPRESS)
    a = ap.parse_args()
    PORT = a.port
    if a.token:
        LOCAL_API_TOKEN = a.token

    if a.once:
        data = XT.synth(a.once)
        Path(a.out).write_bytes(data)
        print("written %s (%d bytes)" % (a.out, len(data)))
        return 0
    if a.selftest:
        print(json.dumps(XT.info(), ensure_ascii=False, indent=2))
        data = XT.synth("你好，我是小雅。语音服务自检通过。")
        print("selftest ok, wav bytes =", len(data))
        return 0

    if a.preload:
        with XT._lock:
            XT._load_locked()

    if not LOCAL_API_TOKEN:
        print("拒绝启动：请由 voicebox 拉起，或设置 CIVENTHRA_LOCAL_API_TOKEN。")
        return 2
    if not is_loopback_host(a.host):
        print("拒绝启动：TTS 服务只能绑定回环地址。")
        return 2

    XT.start_idle_reaper()
    srv = ThreadingHTTPServer((a.host, a.port), _Handler)
    srv.daemon_threads = True
    print("小雅 TTS 就绪： http://%s:%d   （模型懒加载，首次合成约 8 秒）" % (a.host, a.port),
          flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        XT._stop.set()
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
