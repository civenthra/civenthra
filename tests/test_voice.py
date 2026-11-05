# -*- coding: utf-8 -*-
"""语音链路的回归测试。跑法：

    python tests/test_voice.py              # 全部（含在线服务，需要 whisper/GPU）
    python tests/test_voice.py --offline    # 只跑不需要起服务的部分

覆盖四件事，都是踩过或差点踩的：
  1. 本地可用性 —— Python 3.12 里 faster-whisper / SAPI 语音到底在不在；
  2. **闭环识别** —— 合成一句话，再识别回来。这是唯一能证明「链路通」的判据；
  3. 服务与路由 —— sidecar 能被拉起、8765 的三个语音接口都在；
  4. **进程出站限制** —— guard 生效时回环调用必须仍然可用（否则网页直接聋哑），
     而对外调用必须仍然被拦。

⚠️ 不要用「TTS 回灌的识别结果」判断识别质量：系统语音是机器人腔，
   品牌词可能被错误转写。判质量要真人说话（见 /voicecheck）。
"""
from __future__ import annotations

import argparse
import array
import io
import json
import re
import sys
import time
import urllib.request
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAIL = []


def chk(name, cond, detail=""):
    print("  %-56s %s%s" % (name, "PASS" if cond else "FAIL",
                            ("  " + detail) if detail and not cond else ""))
    if not cond:
        FAIL.append(name)


# ── 1. 本地可用性 ───────────────────────────────────────────────────────────

def part_local():
    print("\n[1] 本地语音组件")
    from civenthra import config as C

    py = Path(str(C.VOICE_PYTHON))
    chk("配置里的语音解释器存在", py.exists() or str(py) == "python", str(py))

    import subprocess
    probe = ("import faster_whisper, ctranslate2, numpy;"
             "print(faster_whisper.__version__, ctranslate2.__version__)")
    try:
        out = subprocess.run([str(py), "-X", "utf8", "-c", probe],
                             capture_output=True, text=True, timeout=90)
        ok = out.returncode == 0
        chk("该解释器有 faster-whisper + ctranslate2", ok,
            (out.stderr or "")[-120:] if not ok else "")
    except Exception as e:                                    # noqa: BLE001
        chk("该解释器有 faster-whisper + ctranslate2", False, str(e)[:120])

    try:
        # ⚠ SAPI 检查必须**在语音解释器里**做：主程序（3.13）里没有 pywin32。
        #   这本身就是这个架构的由来，所以测试也照这个事实写。
        sapiprobe = (
            "import pythoncom, win32com.client as w; pythoncom.CoInitialize();"
            "sp=w.Dispatch('SAPI.SpVoice'); vs=sp.GetVoices();"
            "print('|'.join(vs.Item(i).GetDescription() for i in range(vs.Count)))"
        )
        out = subprocess.run([str(py), "-X", "utf8", "-c", sapiprobe],
                             capture_output=True, text=True, timeout=90)
        names = [n for n in (out.stdout or "").strip().split("|") if n]
        chk("系统有 SAPI 语音（离线朗读用）", out.returncode == 0 and bool(names),
            (out.stderr or "")[-120:])
        chk("其中含中文女声", any("Chinese" in n for n in names), str(names))
    except Exception as e:                                    # noqa: BLE001
        chk("系统有 SAPI 语音（离线朗读用）", False, str(e)[:120])

    # whisper 模型是否已在本地缓存（离线前提）
    from civenthra import voice as V
    cache = Path.home() / ".cache" / "huggingface" / "hub"
    hits = list(cache.glob("models--*faster-whisper-%s*" % V.C.VOICE_ASR_MODEL))
    chk("ASR 模型已在本机缓存（离线可用）", bool(hits),
        "找不到 models--*faster-whisper-%s*" % V.C.VOICE_ASR_MODEL)


# ── 2/3. 闭环 + 服务与路由 ──────────────────────────────────────────────────

def _wav_to_16k_pcm(wav_bytes: bytes) -> bytes:
    """纯标准库重采样到 16k 单声道 int16 —— 主程序（3.13）里没有 numpy。"""
    wf = wave.open(io.BytesIO(wav_bytes))
    sr, ch = wf.getframerate(), wf.getnchannels()
    a = array.array("h")
    a.frombytes(wf.readframes(wf.getnframes()))
    if ch > 1:
        a = array.array("h", [sum(a[i:i + ch]) // ch for i in range(0, len(a), ch)])
    n = int(len(a) * 16000 / sr)
    out = array.array("h")
    for k in range(n):
        p = k * sr / 16000.0
        i0 = int(p)
        i1 = min(i0 + 1, len(a) - 1)
        f = p - i0
        out.append(int(a[i0] * (1 - f) + a[i1] * f))
    return out.tobytes()


def part_pipeline():
    print("\n[2] 语音闭环（合成 → 识别）")
    from civenthra import voice

    v = voice.client()
    t0 = time.time()
    ok = v.ensure()
    chk("sidecar 能拉起并响应", ok, "%.1fs" % (time.time() - t0))
    if not ok:
        return

    info = v.info(refresh=True)
    chk("sidecar 报告 ASR/TTS 能力", bool(info and info.get("asr") and info.get("tts")))

    text = "文明穹顶语音链路测试。文明重启的最小依赖集。"
    t0 = time.time()
    got = v.tts(text, engine="sapi")
    if not got:
        chk("TTS 返回音频", False)
        return
    data, mime, used = got
    chk("TTS 返回音频", len(data) > 2000 and mime.startswith("audio/"),
        "%s / %d 字节" % (mime, len(data)))
    chk("TTS 引擎正确（默认离线 SAPI）", used == "sapi", used)
    print("        合成 %.2fs，%d 字节，引擎 %s" % (time.time() - t0, len(data), used))

    pcm = _wav_to_16k_pcm(data)
    t0 = time.time()
    out = v.asr(pcm, 16000)
    if not out:
        chk("ASR 返回结果", False)
        return
    heard = out.get("text", "")
    chk("ASR 返回结果", bool(heard), json.dumps(out, ensure_ascii=False)[:120])
    # 判据放宽松：机器人腔可能误写品牌词，但「文明」「依赖」这类实词应当稳定
    hit = sum(1 for kw in ("文明", "穹顶", "重启", "依赖") if kw in heard)
    chk("识别结果含预期实词（≥2 个）", hit >= 2,
        "只命中 %d 个：「%s」" % (hit, heard))
    print("        识别 %.2fs（含首次载模型）：「%s」" % (time.time() - t0, heard))

    t0 = time.time()
    out2 = v.asr(pcm, 16000)
    dt = time.time() - t0
    chk("二次识别走常驻模型（应明显快）", out2 is not None and dt < 3.0, "%.2fs" % dt)
    print("        二次识别 %.2fs" % dt)

    print("\n[3] 网页侧接口（8765）")
    base = "http://127.0.0.1:%d" % 8765
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    page = op.open(base + "/", timeout=20).read().decode("utf-8")
    match = re.search(r"const CIVENTHRA_SESSION_TOKEN = (\"[^\"]+\");", page)
    token = json.loads(match.group(1)) if match else ""

    def get(path):
        request = urllib.request.Request(
            base + path, headers={"X-Civenthra-Token": token}
        )
        return op.open(request, timeout=20)

    try:
        d = json.loads(get("/api/info").read().decode())
        chk("/api/info 索引在线", d.get("chunks", 0) > 0 and d.get("airgap") is True,
            json.dumps(d))
    except Exception as e:                                    # noqa: BLE001
        chk("/api/info 索引在线", False, "%s（主程序没起？python -m civenthra.serve）" % str(e)[:80])

    try:
        d = json.loads(get("/api/voice/info").read().decode())
        chk("/api/voice/info 语音就绪", bool(d.get("ok")) or bool(d.get("starting")),
            json.dumps(d, ensure_ascii=False)[:140])
    except Exception as e:                                    # noqa: BLE001
        chk("/api/voice/info 语音就绪", False, str(e)[:100])

    try:
        r = op.open(urllib.request.Request(
            base + "/api/voice/tts",
            data=json.dumps({"text": "接口测试"}).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "X-Civenthra-Token": token}), timeout=30)
        chk("/api/voice/tts 出音频", r.status == 200 and len(r.read()) > 1000)
    except Exception as e:                                    # noqa: BLE001
        chk("/api/voice/tts 出音频", False, str(e)[:100])

    try:
        r = op.open(urllib.request.Request(
            base + "/api/voice/asr", data=pcm,
            headers={"Content-Type": "application/octet-stream",
                     "X-Sample-Rate": "16000",
                     "X-Civenthra-Token": token}), timeout=180)
        d = json.loads(r.read().decode())
        chk("/api/voice/asr 出文本", bool(d.get("text")), json.dumps(d, ensure_ascii=False)[:120])
    except Exception as e:                                    # noqa: BLE001
        chk("/api/voice/asr 出文本", False, str(e)[:100])


# ── 4. 离线口径 ─────────────────────────────────────────────────────────────

def part_airgap():
    print("\n[4] 进程出站限制（guard 生效时回环仍须可用）")
    from civenthra import airgap

    airgap.engage()
    try:
        # 回环必须放行，否则语音/嵌入服务全废
        import socket
        try:
            s = socket.create_connection(("127.0.0.1", int(__import__("os").environ.get(
                "VOICE_PORT", "8790"))), timeout=3)
            s.close()
            chk("guard 生效时回环仍可连（语音 sidecar 必需）", True)
        except Exception as e:                                # noqa: BLE001
            chk("guard 生效时回环仍可连（语音 sidecar 必需）", False, str(e)[:100])

        try:
            urllib.request.urlopen("https://example.com", timeout=4)
            chk("guard 生效时对外仍被拦", False, "竟然连上了")
        except Exception:
            chk("guard 生效时对外仍被拦", True)

        try:
            base = "http://127.0.0.1:%d" % 8765
            page = urllib.request.urlopen(base + "/", timeout=6).read().decode("utf-8")
            match = re.search(r"const CIVENTHRA_SESSION_TOKEN = (\"[^\"]+\");", page)
            token = json.loads(match.group(1)) if match else ""
            request = urllib.request.Request(
                base + "/api/voice/info", headers={"X-Civenthra-Token": token}
            )
            urllib.request.urlopen(request, timeout=6)
            chk("guard 生效时能读本机 8765 接口", True)
        except Exception as e:                                # noqa: BLE001
            chk("guard 生效时能读本机 8765 接口", False, str(e)[:100])
    finally:
        airgap.release()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="只跑不需要服务的部分")
    a = ap.parse_args()

    print("══ 文明穹顶 · 语音链路回归 ══════════════════════════════════")
    part_local()
    if not a.offline:
        part_pipeline()
        part_airgap()

    print("\n" + "─" * 64)
    if FAIL:
        print("失败 %d 项：" % len(FAIL))
        for f in FAIL:
            print("  · %s" % f)
        return 1
    print("全部通过 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
