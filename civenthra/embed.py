# -*- coding: utf-8 -*-
"""embed · 稠密检索（可选增强层）。

BM25 的短板是「词面命中，语义跑偏」：问「食物怎么保存才不会坏」，它会把
《泰阿泰德》里"万物得以保存"排到前面——因为「保存」两个字确实出现在那儿。
这是词袋模型的固有缺陷，加多少规则都治不好。

解法是引入语义向量。但纯 Python 做全量稠密检索太慢（8000 块 × 1024 维 = 800 万次
乘加，每次查询要好几秒）。所以这里用的是**两段式**：

    BM25 负责召回（快、全、不会漏）→ 取前 300 块
    向量只负责重排这 300 块（准、慢一点也无所谓）

300 × 1024 = 30 万次乘加，纯 Python 约 0.1 秒。既拿到了语义精度，又不需要 numpy。

嵌入模型走本地 llama-server 的 /v1/embeddings。此代码路径只使用回环地址；整机隔离
仍取决于操作系统网络策略或物理断网。
"""
from __future__ import annotations

import array
import json
import math
import os
import struct
import time
import urllib.request
from pathlib import Path
from typing import List, Optional, Sequence

from . import config as C
from .local_api import free_loopback_port, loopback_port_is_open

# Qwen3-Embedding 官方推荐的检索用法：只在查询侧加指令前缀，文档侧裸文本。
QUERY_INSTRUCTION = "给定一个关于人类文明的问题，检索能够回答它的文献段落"


class EmbedServer:
    """专用嵌入服务（与生成服务分端口，互不干扰）。"""

    def __init__(self, model=None, port: int = None):
        self.model = Path(model or C.EMBED_MODEL)
        configured_port = C.EMBED_PORT if port is None else port
        self.port = int(configured_port) or free_loopback_port()
        self.proc = None
        self._started = False

    @property
    def base(self) -> str:
        return "http://127.0.0.1:%d" % self.port

    def is_up(self) -> bool:
        try:
            req = urllib.request.Request(self.base + "/health")
            with urllib.request.urlopen(req, timeout=3) as r:
                return json.loads(r.read().decode("utf-8", "ignore")).get("status") == "ok"
        except Exception:
            return False

    def start(self, wait: float = 180.0) -> bool:
        if loopback_port_is_open(self.port):
            raise RuntimeError(
                "拒绝复用端口 %d 上身份不明的服务；请关闭占用进程或更换端口" % self.port
            )
        if not self.model.exists():
            raise FileNotFoundError(
                "找不到嵌入模型：%s\n请先下载 Qwen3-Embedding-0.6B-Q8_0.gguf"
                "（一次性，之后永久离线可用）" % self.model)
        cmd = [str(C.LLAMA_SERVER), "-m", str(self.model),
               "--host", "127.0.0.1", "--port", str(self.port),
               "-ngl", str(C.NGL), "-c", "4096", "--embeddings",
               "--no-webui", "--log-disable"]
        import subprocess
        env = dict(os.environ)
        for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                  "http_proxy", "https_proxy", "all_proxy"):
            env.pop(k, None)
        env["NO_PROXY"] = "*"
        self.proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, env=env,
            creationflags=(0x00000008 if os.name == "nt" else 0))
        self._started = True
        t0 = time.time()
        while time.time() - t0 < wait:
            if self.proc.poll() is not None:
                raise RuntimeError("嵌入服务启动失败（返回码 %s）" % self.proc.returncode)
            if self.is_up():
                return True
            time.sleep(0.5)
        raise TimeoutError("嵌入服务未在 %.0fs 内就绪" % wait)

    def stop(self):
        if self.proc and self._started and self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=8)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass

    def embed(self, texts: Sequence[str], timeout: float = 900.0) -> List[List[float]]:
        payload = {"input": list(texts), "model": "local"}
        req = urllib.request.Request(
            self.base + "/v1/embeddings",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8", "ignore"))
        data = sorted(d["data"], key=lambda x: x.get("index", 0))
        return [row["embedding"] for row in data]

    def embed_query(self, q: str) -> List[float]:
        return self.embed(["Instruct: %s\nQuery: %s" % (QUERY_INSTRUCTION, q)])[0]


# ── 向量库（纯 array，无 numpy）──────────────────────────────────────────────

def _l2norm(v: List[float]) -> List[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def save_vectors(path: Path, vecs: List[List[float]], dim: int):
    """float32 小端裸露存储：N×dim。"""
    a = array.array("f")
    for v in vecs:
        a.extend(v)
    with open(path, "wb") as f:
        f.write(struct.pack("<II", len(vecs), dim))
        a.tofile(f)


def load_vectors(path: Path):
    with open(path, "rb") as f:
        head = f.read(8)
        n, dim = struct.unpack("<II", head)
        a = array.array("f")
        a.fromfile(f, n * dim)
    return a, n, dim


def build_dense(records: List[dict], out_dir: Path, server: EmbedServer,
                batch: int = 8, log=print) -> int:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    texts = [r["text"] for r in records]
    vecs: List[List[float]] = []
    t0 = time.time()
    for i in range(0, len(texts), batch):
        chunk = texts[i:i + batch]
        try:
            out = server.embed(chunk)
        except Exception as e:
            log("  批 %d 失败（%s），逐条重试" % (i, str(e)[:60]))
            out = []
            for t in chunk:
                out.append(server.embed([t])[0])
        vecs.extend(_l2norm(v) for v in out)
        if (i // batch) % 40 == 0:
            done = len(vecs)
            el = time.time() - t0
            eta = el / max(done, 1) * (len(texts) - done)
            log("    %d/%d  %.0fs  余约 %.0fs" % (done, len(texts), el, eta))
    dim = len(vecs[0]) if vecs else 0
    save_vectors(out_dir / "dense.bin", vecs, dim)
    (out_dir / "dense.json").write_text(json.dumps(
        {"n": len(vecs), "dim": dim, "model": Path(server.model).name,
         "instruction": QUERY_INSTRUCTION,
         "built_at": time.strftime("%Y-%m-%d %H:%M:%S")},
        ensure_ascii=False, indent=2), encoding="utf-8")
    return len(vecs)


class DenseIndex:
    def __init__(self, arr, n: int, dim: int, meta: dict):
        self.a = arr
        self.n = n
        self.dim = dim
        self.meta = meta

    @classmethod
    def load(cls, index_dir: Path) -> Optional["DenseIndex"]:
        index_dir = Path(index_dir)
        p = index_dir / "dense.bin"
        if not p.exists():
            return None
        arr, n, dim = load_vectors(p)
        meta = json.loads((index_dir / "dense.json").read_text(encoding="utf-8"))
        return cls(arr, n, dim, meta)

    def scores(self, qvec: List[float], ids: Sequence[int]) -> List[float]:
        import operator
        a, dim = self.a, self.dim
        out = []
        for did in ids:
            off = did * dim
            out.append(sum(map(operator.mul, qvec, a[off:off + dim])))
        return out
