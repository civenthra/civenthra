# -*- coding: utf-8 -*-
"""voicebox · 文明穹顶的本地语音服务（ASR + TTS），独立进程。

为什么是独立进程
----------------
ASR 用的 faster-whisper 装在本机 **Python 3.12** 的用户级 site-packages 里
（例如 `%APPDATA%\\Python\\Python312`），而文明穹顶主程序跑的是 3.13。
带 C 扩展的二进制包搬不过去，所以按「谁有工具谁来干」拆开：
主程序 8765，语音 8790，两个都只绑 127.0.0.1。零安装 —— 全部复用机器上已有的东西。

本地优先边界
------------
· ASR = faster-whisper large-v3（本地 HF 缓存 ≈3GB）+ CUDA float16。实测：
  加载 4.5s、2.64 秒音频识别 0.94s、结果一字不差。
· TTS = Windows SAPI（系统自带中文女声 Microsoft Huihui），
  实测合成 6.23 秒语音只要 0.05 秒。零依赖、断网可用。
· edge-tts 需要联网；当主程序启用进程出站限制时，本服务会禁用它并回落 SAPI。
  严格整机隔离仍须操作系统防火墙、隔离网络或物理断网。

接口
----
GET  /health          → {"ok":true,"asr":{...},"tts":{"sapi":[...],"edge":bool}}
POST /asr             → 请求体 = 原始 PCM，int16 小端，单声道
                        头 X-Sample-Rate 指定采样率（默认 16000）
                        返回 {"text":...,"seconds":...,"elapsed_ms":...}
POST /tts             → JSON {"text":...,"engine":"sapi|edge","voice":...,"rate":0}
                        返回音频字节（sapi → audio/wav，edge → audio/mpeg）

自检
----
    <python312> voicebox.py --selftest          # 只在进程内测，不起服务
    <python312> voicebox.py                     # 起服务（默认 8790）
    <python312> voicebox.py --selftest --serve  # 起服务顺带自检
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import tempfile
import threading
import time
import wave
from typing import Optional

import numpy as np

from civenthra.local_api import (
    TOKEN_ENV, TOKEN_HEADER, host_header_is_local, is_loopback_host,
    loopback_http_base_url, token_matches,
)

# ⚠ FastAPI 的类型提示必须在**模块级**名字空间里解析得到。
# 本文件开头是 `from __future__ import annotations`，所有注解都成了字符串；
# FastAPI 随后用 func.__globals__ 去解析 `request: Request`，
# 若 Request 只是 build_app() 里的局部导入 → 解析失败 → FastAPI 退化成
# 「把它当成一个必填的 query 参数」，返回 422 {"loc":["query","request"]}。
# 实测踩过一次，所以这里就在模块级导入。
try:  # 纯 ASR/TTS 用法（--selftest）不该被 fastapi 缺失拖累
    from fastapi import FastAPI, Request, Response
    from fastapi.responses import JSONResponse
except Exception:                                     # noqa: BLE001
    FastAPI = Request = Response = JSONResponse = None

# ── 配置（环境变量可覆盖，与主程序 config.py 的 VOICE_* 前缀一致）──────────────
HOST = os.environ.get("VOICE_HOST", "127.0.0.1")
PORT = int(os.environ.get("VOICE_PORT", "8790"))
ASR_MODEL = os.environ.get("VOICE_ASR_MODEL", "large-v3")
ASR_DEVICE = os.environ.get("VOICE_ASR_DEVICE", "cuda")
ASR_COMPUTE = os.environ.get("VOICE_ASR_COMPUTE", "float16")
ASR_LANG = os.environ.get("VOICE_ASR_LANG", "zh")
ASR_BEAM = int(os.environ.get("VOICE_ASR_BEAM", "1"))
# 多久没人用就把模型从显存里卸掉（秒）。0 = 常驻。
IDLE_UNLOAD_S = float(os.environ.get("VOICE_IDLE_UNLOAD", "900"))
SAPI_VOICE = os.environ.get("VOICE_SAPI_VOICE", "Huihui")
EDGE_VOICE = os.environ.get("VOICE_EDGE_VOICE", "zh-CN-XiaoxiaoNeural")
LOCAL_API_TOKEN = os.environ.get(TOKEN_ENV, "")
NETWORK_GUARD = os.environ.get("CIVENTHRA_PROCESS_NETWORK_GUARD") == "1"
MAX_ASR_BYTES = int(os.environ.get("VOICE_MAX_ASR_BYTES", str(16 * 1024 * 1024)))
MAX_JSON_BYTES = int(os.environ.get("VOICE_MAX_JSON_BYTES", str(64 * 1024)))

# ── 小雅音色（XTTS v2 语音克隆）────────────────────────────────────────────
# 跑在**独立 venv** 里的另一个 sidecar：coqui-tts 要 transformers<5，和本环境打架，
# 所以它自己一套环境（.workbuddy\venvs\xiaoya-tts），本文件只做 HTTP 转发。
XIAOYA_URL = loopback_http_base_url(
    os.environ.get("VOICE_XIAOYA_URL", "http://127.0.0.1:8791")
)
XIAOYA_PYTHON = os.environ.get("VOICE_XIAOYA_PYTHON", sys.executable)
XIAOYA_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "xiaoya_tts.py")
XIAOYA_AUTOSTART = os.environ.get("VOICE_XIAOYA_AUTOSTART", "1") not in ("0", "false", "False")

# 引擎把「作业说明」或整段乱码吐出来时的兜底：单条文本上限，防炸
MAX_TTS_CHARS = int(os.environ.get("VOICE_MAX_TTS_CHARS", "600"))


# ══════════════════════════════════════════════════════════════════════════
# ASR —— faster-whisper
# ══════════════════════════════════════════════════════════════════════════

class _Asr:
    """懒加载 + 线程安全 + 空闲卸载。显存宝贵，别一直占着。"""

    def __init__(self):
        self._model = None
        self._lock = threading.Lock()
        self._loaded_with: Optional[tuple] = None
        self._last_use = 0.0
        self._loading = False

    @property
    def ready(self) -> bool:
        return self._model is not None

    def status(self) -> dict:
        return {
            "ready": self._model is not None,
            "loading": self._loading,
            "model": ASR_MODEL,
            "device": (self._loaded_with or (ASR_DEVICE, ASR_COMPUTE))[0],
            "compute": (self._loaded_with or (ASR_DEVICE, ASR_COMPUTE))[1],
        }

    def _load_locked(self):
        """必须在 self._lock 内调用。"""
        if self._model is not None:
            return
        from faster_whisper import WhisperModel  # 延迟到真正要用才 import
        self._loading = True
        try:
            tries = [(ASR_DEVICE, ASR_COMPUTE)]
            if ASR_DEVICE != "cpu":
                # GPU 跑不动（驱动/CUDA 版本不合）时别整个服务挂掉
                tries.append(("cpu", "int8"))
            last = None
            for dev, ct in tries:
                t0 = time.time()
                try:
                    self._model = WhisperModel(ASR_MODEL, device=dev, compute_type=ct)
                    self._loaded_with = (dev, ct)
                    print("  [asr] 载入 %s @ %s/%s  %.1fs"
                          % (ASR_MODEL, dev, ct, time.time() - t0), flush=True)
                    return
                except Exception as e:            # noqa: BLE001 —— 降级路径
                    last = e
                    print("  [asr] %s/%s 载入失败：%s" % (dev, ct, str(e)[:160]), flush=True)
            raise RuntimeError("ASR 模型载入失败：%s" % last)
        finally:
            self._loading = False

    def transcribe(self, pcm: bytes, sample_rate: int = 16000) -> dict:
        audio = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        seconds = len(audio) / float(sample_rate or 16000)
        if len(audio) < int(0.30 * sample_rate):     # <0.3 秒当误触
            return {"text": "", "seconds": round(seconds, 2), "elapsed_ms": 0,
                    "skipped": "too_short"}
        with self._lock:
            self._load_locked()
            t0 = time.time()
            segments, _info = self._model.transcribe(
                audio, language=(ASR_LANG or None), beam_size=ASR_BEAM,
                vad_filter=True, condition_on_previous_text=False,
            )
            text = "".join(s.text for s in segments).strip()
            ms = int((time.time() - t0) * 1000)
            self._last_use = time.time()
        return {"text": text, "seconds": round(seconds, 2), "elapsed_ms": ms,
                "device": self._loaded_with}

    def unload(self):
        with self._lock:
            self._model = None
            self._loaded_with = None
        _gc()

    def maybe_unload(self):
        if not IDLE_UNLOAD_S or self._model is None:
            return
        if time.time() - self._last_use > IDLE_UNLOAD_S:
            print("  [asr] 空闲 %.0f 分钟，卸载模型释放显存" % (IDLE_UNLOAD_S / 60), flush=True)
            self.unload()


def _gc():
    import gc
    gc.collect()
    try:
        import torch
        torch.cuda.empty_cache()
    except Exception:
        pass


ASR = _Asr()


# ══════════════════════════════════════════════════════════════════════════
# TTS —— SAPI（离线默认）/ edge-tts（可选，需联网）
# ══════════════════════════════════════════════════════════════════════════

class _Sapi:
    """Windows 自带语音。COM 是**线程亲和**的，每个线程都要自己 CoInitialize。"""

    # SAFT22kHz16BitMono —— 中文女声最舒服的档位
    _FMT_MONO16_22K = 22

    def __init__(self):
        self._tls = threading.local()

    def _synth(self):
        import pythoncom
        import win32com.client as w
        pythoncom.CoInitialize()                     # 每个线程一次
        sp = w.Dispatch("SAPI.SpVoice")
        voices = sp.GetVoices()
        picked = None
        for i in range(voices.Count):
            desc = voices.Item(i).GetDescription()
            if SAPI_VOICE.lower() in desc.lower():
                picked = (i, desc)
                break
        if picked:
            sp.Voice = voices.Item(picked[0])
        self._tls.sp = sp
        return sp

    def voices(self) -> list:
        try:
            import pythoncom
            import win32com.client as w
            pythoncom.CoInitialize()
            sp = w.Dispatch("SAPI.SpVoice")
            vs = sp.GetVoices()
            return [vs.Item(i).GetDescription() for i in range(vs.Count)]
        except Exception as e:                        # noqa: BLE001
            return ["(不可用：%s)" % str(e)[:60]]

    def speak(self, text: str, rate: int = 0) -> bytes:
        import pythoncom
        import win32com.client as w
        sp = getattr(self._tls, "sp", None) or self._synth()
        handle, tmp = tempfile.mkstemp(prefix="civenthra-sapi-", suffix=".wav")
        os.close(handle)
        os.remove(tmp)  # SAPI creates the file; the random name prevents request collisions.
        stream = None
        old = None
        try:
            stream = w.Dispatch("SAPI.SpFileStream")
            fmt = stream.Format
            fmt.Type = self._FMT_MONO16_22K
            stream.Format = fmt
            stream.Open(tmp, 3)                       # SSFMCreateForWrite
            old = sp.AudioOutputStream
            sp.AudioOutputStream = stream
            sp.Rate = int(rate)
            sp.Speak(text)
            stream.Close()
            stream = None
            with open(tmp, "rb") as output:
                return output.read()
        finally:
            if old is not None:
                sp.AudioOutputStream = old
            if stream is not None:
                try:
                    stream.Close()
                except Exception:                     # noqa: BLE001
                    pass
            try:
                os.remove(tmp)
            except FileNotFoundError:
                pass


SAPI = _Sapi()


def edge_available() -> bool:
    if NETWORK_GUARD:
        return False
    try:
        import edge_tts  # noqa: F401
        return True
    except Exception:
        return False


async def _edge_synth(text: str, voice: str, rate: int = 0) -> bytes:
    import edge_tts
    rate_str = "%+d%%" % (int(rate) * 10)             # SAPI 的 -10..10 → edge 的百分比
    comm = edge_tts.Communicate(text, voice, rate=rate_str)
    buf = io.BytesIO()
    async for chunk in comm.stream():
        if chunk.get("type") == "audio":
            buf.write(chunk["data"])
    return buf.getvalue()


# ── 小雅音色：转发到独立 venv 里的 XTTS sidecar ────────────────────────────
_xiaoya_state = {"starting": False}
_xiaoya_lock = threading.Lock()
_xiaoya_cache = {"t": 0.0, "v": None}


def xiaoya_ready(timeout: float = 1.5, ttl: float = 0.0):
    """探一下小雅 sidecar。ttl>0 时复用最近一次结果（给 /health 这类高频调用用）。"""
    now = time.time()
    if ttl > 0 and _xiaoya_cache["v"] is not None and (now - _xiaoya_cache["t"]) < ttl:
        return _xiaoya_cache["v"]
    import urllib.request
    try:
        req = urllib.request.Request(
            XIAOYA_URL + "/health", headers={TOKEN_HEADER: LOCAL_API_TOKEN}
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            v = json.loads(r.read().decode("utf-8"))
        if v.get("service") != "civenthra-xiaoya-tts" or v.get("protocol") != 1:
            v = None
    except Exception:
        v = None
    _xiaoya_cache["t"], _xiaoya_cache["v"] = now, v
    return v


def ensure_xiaoya() -> bool:
    """没起就点火拉起。XTTS 首次要加载约 8 秒，所以这里只负责点、不等。"""
    if xiaoya_ready(0.4):
        return True
    if not XIAOYA_AUTOSTART:
        return False
    with _xiaoya_lock:
        if _xiaoya_state["starting"]:
            return False
        _xiaoya_state["starting"] = True
    try:
        if not os.path.exists(XIAOYA_PYTHON) or not os.path.exists(XIAOYA_SCRIPT):
            print("  [tts] 小雅 sidecar 缺文件：%s / %s" % (XIAOYA_PYTHON, XIAOYA_SCRIPT),
                  flush=True)
            return False
        import subprocess
        log = open(os.path.join(os.path.dirname(XIAOYA_SCRIPT), "xiaoya_tts.log"), "ab")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        try:
            subprocess.Popen([XIAOYA_PYTHON, "-X", "utf8", "-u", XIAOYA_SCRIPT],
                             cwd=os.path.dirname(XIAOYA_SCRIPT),
                             stdout=log, stderr=log, creationflags=flags, env=env)
        finally:
            log.close()
        print("  [tts] 小雅 sidecar 启动中（XTTS 首次加载约 8 秒）", flush=True)
        return True
    except Exception as e:                            # noqa: BLE001
        print("  [tts] 拉起小雅 sidecar 失败：%s" % str(e)[:160], flush=True)
        return False
    finally:
        with _xiaoya_lock:
            _xiaoya_state["starting"] = False


def _xiaoya_synth(text: str, speed: float = 1.0) -> bytes:
    import urllib.request
    payload = json.dumps({"text": text, "speed": speed}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        XIAOYA_URL + "/tts", data=payload,
        headers={"Content-Type": "application/json", TOKEN_HEADER: LOCAL_API_TOKEN},
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        return r.read()


def synthesize(text: str, engine: str = "sapi", voice: str = None,
               rate: int = 0) -> tuple[bytes, str, str]:
    """返回 (音频字节, MIME, 真正用上的引擎)。edge / xiaoya 失败一律回落 sapi。"""
    text = (text or "").strip()
    if not text:
        return b"", "audio/wav", engine
    if len(text) > MAX_TTS_CHARS:
        text = text[:MAX_TTS_CHARS]

    if engine == "xiaoya":
        try:
            if not xiaoya_ready(0.6):
                ensure_xiaoya()
                raise RuntimeError("小雅语音正在启动，约 8 秒后可用")
            data = _xiaoya_synth(text, speed=max(0.5, min(1.6, 1.0 + rate / 10.0)))
            if data:
                return data, "audio/wav", "xiaoya"
            print("  [tts] 小雅返回空音频，回落 sapi", flush=True)
        except Exception as e:                        # noqa: BLE001
            print("  [tts] 小雅失败（%s），回落 sapi" % str(e)[:140], flush=True)

    if engine == "edge" and edge_available():
        try:
            import asyncio
            data = asyncio.run(_edge_synth(text, voice or EDGE_VOICE, rate))
            if data:
                return data, "audio/mpeg", "edge"
            print("  [tts] edge 返回空音频，回落 sapi", flush=True)
        except Exception as e:                        # noqa: BLE001
            print("  [tts] edge 失败（%s），回落 sapi" % str(e)[:120], flush=True)

    return SAPI.speak(text, rate=rate), "audio/wav", "sapi"


# ══════════════════════════════════════════════════════════════════════════
# HTTP 服务
# ══════════════════════════════════════════════════════════════════════════

def build_app():
    from contextlib import asynccontextmanager

    if FastAPI is None:
        raise RuntimeError("fastapi 未安装，无法起 HTTP 服务")
    if not LOCAL_API_TOKEN:
        raise RuntimeError("缺少 CIVENTHRA_LOCAL_API_TOKEN，拒绝启动无鉴权的本地服务")

    @asynccontextmanager
    async def lifespan(_app):
        def loop():
            while True:
                time.sleep(60)
                try:
                    ASR.maybe_unload()
                except Exception:                     # noqa: BLE001
                    pass
        threading.Thread(target=loop, daemon=True, name="idle-unload").start()
        yield

    app = FastAPI(title="civenthra voicebox", docs_url=None, redoc_url=None,
                  lifespan=lifespan)
    slots = threading.BoundedSemaphore(2)

    @app.middleware("http")
    async def protect_loopback(request: Request, call_next):
        remote = request.client.host if request.client else None
        host = request.headers.get("host")
        if (not is_loopback_host(remote) or not host_header_is_local(host, PORT)
                or request.headers.get("origin")
                or not token_matches(request.headers.get(TOKEN_HEADER), LOCAL_API_TOKEN)):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        if request.headers.get("transfer-encoding"):
            return JSONResponse({"error": "chunked body rejected"}, status_code=400)
        try:
            content_length = int(request.headers.get("content-length") or 0)
        except ValueError:
            return JSONResponse({"error": "invalid content length"}, status_code=400)
        limit = MAX_ASR_BYTES if request.url.path == "/asr" else MAX_JSON_BYTES
        if content_length < 0 or content_length > limit:
            return JSONResponse({"error": "request body too large"}, status_code=413)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get("/health")
    def health():
        xy = xiaoya_ready(0.35, ttl=5.0)
        return {
            "ok": True, "service": "civenthra-voicebox", "protocol": 1,
            "asr": ASR.status(),
            "tts": {"sapi": SAPI.voices(), "edge": edge_available(),
                    "xiaoya": bool(xy),
                    "xiaoya_ready": bool(xy and xy.get("ready")),
                    "xiaoya_device": (xy or {}).get("device"),
                    "default_engine": "sapi", "edge_voice": EDGE_VOICE,
                    "idle_unload_s": IDLE_UNLOAD_S},
        }

    @app.post("/asr")
    async def asr(request: Request):
        pcm = await request.body()
        if len(pcm) > MAX_ASR_BYTES or request.headers.get("content-type") != "application/octet-stream":
            return JSONResponse({"error": "invalid audio request"}, status_code=400)
        try:
            sr = int(request.headers.get("X-Sample-Rate") or 16000)
        except ValueError:
            return JSONResponse({"error": "invalid sample rate"}, status_code=400)
        if not 8000 <= sr <= 192000:
            return JSONResponse({"error": "invalid sample rate"}, status_code=400)
        if not slots.acquire(blocking=False):
            return JSONResponse({"error": "busy"}, status_code=429)
        try:
            return ASR.transcribe(pcm, sr)
        except Exception as exc:                       # noqa: BLE001
            print("  [asr] request failed: %s" % str(exc)[:300], flush=True)
            return JSONResponse({"error": "transcription failed"}, status_code=500)
        finally:
            slots.release()

    @app.post("/tts")
    async def tts(request: Request):
        if request.headers.get("content-type", "").split(";", 1)[0] != "application/json":
            return JSONResponse({"error": "unsupported content type"}, status_code=415)
        try:
            raw = await request.body()
            if len(raw) > MAX_JSON_BYTES:
                return JSONResponse({"error": "request body too large"}, status_code=413)
            req = json.loads(raw or b"{}")
        except (UnicodeError, json.JSONDecodeError):
            return JSONResponse({"error": "bad json"}, status_code=400)
        text = req.get("text", "") if isinstance(req, dict) else ""
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TTS_CHARS:
            return JSONResponse({"error": "invalid text"}, status_code=400)
        if not slots.acquire(blocking=False):
            return JSONResponse({"error": "busy"}, status_code=429)
        try:
            data, mime, used = synthesize(
                text, req.get("engine", "sapi"),
                req.get("voice"), int(req.get("rate", 0) or 0))
        except Exception as exc:                       # noqa: BLE001
            print("  [tts] request failed: %s" % str(exc)[:300], flush=True)
            return JSONResponse({"error": "synthesis failed"}, status_code=500)
        finally:
            slots.release()
        if not data:
            return JSONResponse({"error": "empty"}, status_code=400)
        return Response(content=data, media_type=mime,
                        headers={"X-Voice-Engine": used})

    @app.post("/unload")
    def unload():
        ASR.unload()
        return {"ok": True}

    return app


# ══════════════════════════════════════════════════════════════════════════
# 自检
# ══════════════════════════════════════════════════════════════════════════

def selftest(sample_wav: Optional[str] = None) -> int:
    print("── 语音服务自检 ─────────────────────────────")
    print("  faster-whisper :", end=" ")
    try:
        import faster_whisper
        print("v%s" % faster_whisper.__version__)
    except Exception as e:                            # noqa: BLE001
        print("不可用（%s）" % e)

    print("  SAPI 语音      :", SAPI.voices())
    print("  edge-tts       :", "可用（需联网）" if edge_available() else "未安装")

    print("\n  [1/2] TTS 合成 …")
    text = "文明穹顶语音自检。文明重启的最小依赖集。"
    t0 = time.time()
    try:
        data, mime, used = synthesize(text, "sapi")
        print("        OK  %s  %.0f 字节  %.2fs  引擎=%s" % (mime, len(data), time.time() - t0, used))
    except Exception as e:                            # noqa: BLE001
        print("        ✗ 失败：%s" % e)
        return 1

    # 把刚合成的 wav 转成 16k 单声道 PCM，喂回 ASR —— 一个闭环
    print("\n  [2/2] ASR 识别（用上一步合成的音频回灌）…")
    src = sample_wav
    if not src:
        try:
            import soundfile as sf
            audio, sr = sf.read(io.BytesIO(data), dtype="float32")
            if audio.ndim > 1:
                audio = audio.mean(axis=1)
            if sr != 16000:                            # 线性重采样到 16k
                idx = np.linspace(0, len(audio) - 1, int(len(audio) * 16000 / sr))
                audio = np.interp(idx, np.arange(len(audio)), audio).astype(np.float32)
            pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes()
        except Exception as e:                        # noqa: BLE001
            print("        ✗ 准备音频失败：%s" % e)
            return 1
    else:
        with wave.open(src, "rb") as wf:
            raw = wf.readframes(wf.getnframes())
            pcm = raw
            print("        （用外部音频 %s）" % src)
    try:
        t0 = time.time()
        out = ASR.transcribe(pcm, 16000)
        print("        OK  识别=「%s」  音频 %.2fs  耗时 %dms  设备=%s"
              % (out["text"], out["seconds"], out["elapsed_ms"], out.get("device")))
        hit = "文明穹顶" in out["text"] or "文明" in out["text"]
        print("\n  闭环判定：%s" % ("通过 ✅" if hit else "识别文本与原文不符 ⚠（模型/sample 差异，不一定是故障）"))
        return 0 if hit else 2
    except Exception as e:                            # noqa: BLE001
        print("        ✗ 失败：%s" % e)
        return 1


def main(argv=None):
    global PORT, LOCAL_API_TOKEN
    ap = argparse.ArgumentParser(prog="voicebox")
    ap.add_argument("--host", default=HOST)
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--serve", action="store_true",
                    help="自检后继续起服务（默认：只自检、自检完就退）")
    ap.add_argument("--wav", default=None, help="自检用外部 wav（否则自动闭环）")
    ap.add_argument("--token", default=None, help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    PORT = a.port
    if a.token:
        LOCAL_API_TOKEN = a.token

    if a.selftest:
        rc = selftest(a.wav)
        # ⚠ 只有显式给了 --serve 才继续起服务。
        #   最初写成「无参数才 return」，结果 `--selftest` 本身就算「有参数」，
        #   自检跑完一头扎进 uvicorn 阻塞不返回 —— 表现为命令挂死。
        if not a.serve:
            return rc

    import uvicorn
    if not LOCAL_API_TOKEN:
        print("拒绝启动：请由 civenthra.serve 拉起，或设置 CIVENTHRA_LOCAL_API_TOKEN。")
        return 2
    if not is_loopback_host(a.host):
        print("拒绝启动：语音服务只能绑定回环地址。")
        return 2
    print("\n  🎙 voicebox   http://%s:%d   （ASR=%s / TTS=SAPI %s）\n"
          % (a.host, a.port, ASR_MODEL, SAPI_VOICE))
    uvicorn.run(build_app(), host=a.host, port=a.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
