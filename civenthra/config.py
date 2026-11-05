# -*- coding: utf-8 -*-
"""全局配置。

优先级：环境变量 CIVENTHRA_<KEY> > config.local.json > 内置默认值。
所有路径都可以在 config.local.json 里改，代码里不写死任何相对假设。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_local() -> dict:
    p = ROOT / "config.local.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


_LOCAL = _load_local()


def cfg(key: str, default):
    v = os.environ.get("CIVENTHRA_" + key.upper())
    if v not in (None, ""):
        return v
    if key in _LOCAL and _LOCAL[key] not in (None, ""):
        return _LOCAL[key]
    return default


# ── 底本来源 ────────────────────────────────────────────────────────────────
# 默认值不包含开发者账号路径；本机通过 config.local.json 或环境变量覆盖。
BOOKS_LIBRARY = Path(cfg("books_library", str(ROOT.parent / "books" / "library" / "books")))

# ── 推理后端 ────────────────────────────────────────────────────────────────
_MODEL_ROOT = Path(cfg("model_root", str(ROOT / "models")))
LLAMA_SERVER = Path(cfg("llama_server", shutil.which("llama-server") or "llama-server"))
MODEL_MAIN   = Path(cfg("model_main", str(_MODEL_ROOT / "Qwen3-4B-Q4_K_M.gguf")))
MODEL_SMALL  = Path(cfg("model_small", str(_MODEL_ROOT / "Qwen3-1.7B-Q8_0.gguf")))
EMBED_MODEL  = Path(cfg("embed_model", str(_MODEL_ROOT / "Qwen3-Embedding-0.6B-Q8_0.gguf")))

HOST       = str(cfg("host", "127.0.0.1"))     # 只绑回环，永不监听外网
PORT       = int(cfg("port", 0))              # 0 = 每次随机回环端口，降低端口冒充风险
EMBED_PORT = int(cfg("embed_port", 0))
CTX        = int(cfg("ctx", 8192))
NGL        = int(cfg("ngl", 999))              # GPU 层数；纯 CPU 设 0
THREADS    = int(cfg("threads", 8))
WEB_PORT   = int(cfg("web_port", 8765))        # 网页界面端口

# ── 检索参数 ────────────────────────────────────────────────────────────────
CHUNK_CHARS   = int(cfg("chunk_chars", 1200))   # 块大小（字符）
CHUNK_OVERLAP = int(cfg("chunk_overlap", 200))  # 块间重叠
TOP_K         = int(cfg("top_k", 6))            # 送给模型的段落数
PER_BOOK_CAP  = int(cfg("per_book_cap", 2))     # 同一本书最多占几个名额（保证引用多样）

# ── 目录 ────────────────────────────────────────────────────────────────────
INDEX_DIR  = ROOT / "index"
CANON_FILE = ROOT / "canon" / "canon.json"
SAFETY_REVIEW_FILE = ROOT / str(cfg("safety_review_file", "canon/safety-reviews.json"))
GRAPH_MODE = str(cfg("graph_mode", "evidence"))  # off / evidence / hybrid (pilot opt-in)
AUTHORIZATION_FILE = Path(cfg("authorization_file", str(ROOT / "authorizations.local.json")))
DEPLOYMENT_TERRITORY = str(cfg("deployment_territory", ""))


# ── 语音（voicebox sidecar）─────────────────────────────────────────────────
# ASR/TTS 全部是本机现成的：
#   · ASR = faster-whisper large-v3（本地 HF 缓存）+ CUDA
#   · TTS = Windows SAPI 中文女声 Huihui（系统自带，断网可用）
# 但 faster-whisper 只装在 **Python 3.12** 的用户级 site-packages 里，
# 主程序跑的是 3.13 —— 所以语音跑独立进程，主程序通过回环 HTTP 调它。
# airgap 的白名单包含 127.0.0.0/8，回环调用不受影响；这不等同整机离线。
VOICE_PORT     = int(cfg("voice_port", 8790))
VOICE_AUTOSTART = str(cfg("voice_autostart", "1")) not in ("0", "false", "False", "")

# 运行语音 sidecar 的解释器；需要其他环境时在本机配置中覆盖。
VOICE_PYTHON = Path(cfg("voice_python", sys.executable or "python"))
VOICE_SCRIPT = ROOT / "voicebox.py"
VOICE_ASR_MODEL   = str(cfg("voice_asr_model", "large-v3"))
VOICE_ASR_DEVICE  = str(cfg("voice_asr_device", "cuda"))
VOICE_ASR_COMPUTE = str(cfg("voice_asr_compute", "float16"))
VOICE_TTS_ENGINE  = str(cfg("voice_tts_engine", "sapi"))   # sapi（离线）| edge（需联网）
VOICE_TTS_VOICE   = str(cfg("voice_tts_voice", ""))        # 空 = 用服务端默认
VOICE_TTS_RATE    = int(cfg("voice_tts_rate", 0))          # -10..10
XIAOYA_REF_WAV    = Path(cfg("xiaoya_ref_wav", str(ROOT / "assets" / "voice" / "local-reference.wav")))

# 常听模式的唤醒词。ASR 常把「小雅」写成「小鸭/小呀/小亚」，
# 所以前端做的是**宽容匹配**（见 serve.py 的 matchWake），这里只放正字。
VOICE_WAKE_WORD   = str(cfg("voice_wake_word", "小雅"))

# ★ 麦克风诊断落盘（JSONL）。浏览器把「它那一侧实际录到了什么」POST 到
#   /api/voice/report，主程序追加到这里。
#   用途：出问题时服务端只看得见「收到 N 字节」或「识别出空串」，
#   分不清是**没录到声音**还是**录到了但识别不出来** —— 这个文件把它们分开。
#   实测价值：2026-10-04 正是靠它 + pyaudio 侧写，判定「系统给应用的音频就是全零」，
#   从而把根因从「代码/权限」移到「Windows 音频端点」。
MIC_REPORT = ROOT / str(cfg("mic_report", "mic_report.jsonl"))
VOICE_DIAGNOSTICS = str(cfg("voice_diagnostics", "0")).lower() in {"1", "true", "yes"}
VOICE_DIAGNOSTICS_INCLUDE_TEXT = str(
    cfg("voice_diagnostics_include_text", "0")
).lower() in {"1", "true", "yes"}
MIC_REPORT_MAX_BYTES = int(cfg("mic_report_max_bytes", 1_000_000))
