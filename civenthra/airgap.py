# -*- coding: utf-8 -*-
"""airgap · 当前 Python 进程的出站连接限制与自检。

这是整个项目里最重要、也最容易被做成噱头的部分。

这里通过替换 Python 标准库的常用 socket/urllib 入口，拒绝当前进程指向本机之外的
连接与域名解析，并抛出 AirgapError。它是纵深防御，不是操作系统防火墙、容器沙箱、
隔离网络或物理气隙；子进程、原生扩展以及提前保存的底层函数引用不受其完整控制。

放行白名单只有回环地址（127.0.0.0/8、::1、localhost），因为本地推理服务
走的就是回环——这不构成对外通信。

verify() 只验证这些进程内钩子是否生效，不会访问真实外网，也不证明整机断网。
"""
from __future__ import annotations

import os
import ipaddress
import socket
import sys
from typing import List, Tuple

__all__ = ["AirgapError", "engage", "release", "engaged", "verify", "guard"]

_LOCAL_HOSTS = {"localhost"}

# 代理变量：**必须**在封锁时摘掉。
# 这是一个真实踩到的洞：本机 Clash 监听 127.0.0.1:7897，如果 HTTP(S)_PROXY 还在环境里，
# urllib 会把请求发给 127.0.0.1:7897 —— 那是回环地址、在白名单里 —— 然后由代理转发出去。
# 于是进程内限制会被绕过。必须同时清理代理配置与缓存的 opener。
_PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "FTP_PROXY",
               "http_proxy", "https_proxy", "all_proxy", "ftp_proxy",
               "NO_PROXY", "no_proxy")

_saved_env: dict = {}
_engaged = False
_patches: List[Tuple[object, str, object]] = []


def _proxy_var_names():
    """Yield proxy variable names once on Windows' case-insensitive environment."""
    seen = set()
    for name in _PROXY_VARS:
        normalized = name.upper() if os.name == "nt" else name
        if normalized in seen:
            continue
        seen.add(normalized)
        yield name


class AirgapError(OSError):
    """被当前进程的出站限制拦截的访问。"""


def _is_local(host) -> bool:
    if host is None:
        return True
    if isinstance(host, bytes):
        try:
            host = host.decode("utf-8", "ignore")
        except Exception:
            return False
    host = str(host).strip().lower()
    if host in _LOCAL_HOSTS:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        # 不能用 `startswith("127.")`：`127.attacker.example` 会被误判为
        # 回环地址，使 DNS、UDP 或 connect_ex 路径有机会绕过限制。
        return False


def _patch(obj, name, fn):
    _patches.append((obj, name, getattr(obj, name)))
    setattr(obj, name, fn)


def engage():
    """开启当前 Python 进程的出站限制（幂等）。"""
    global _engaged
    if _engaged:
        return
    _engaged = True

    # ① 摘掉代理环境变量（否则请求会从 127.0.0.1 的代理出去，绕过回环白名单）
    for k in _proxy_var_names():
        _saved_env[k] = os.environ.pop(k, None)
    os.environ["NO_PROXY"] = "*"
    os.environ["CIVENTHRA_PROCESS_NETWORK_GUARD"] = "1"

    # ② 掐掉 socket 层的三条通路
    _patch(socket, "getaddrinfo", _gai)
    _patch(socket, "create_connection", _create_connection)
    _patch(socket.socket, "connect", _connect)
    _patch(socket.socket, "connect_ex", _connect_ex)
    _patch(socket.socket, "sendto", _sendto)

    # ③ 有些模块在 import 时就把函数**引用**存了下来（例如 http.client 的
    #    `_create_connection = socket.create_connection`），只改 socket 模块里的名字
    #    管不到它们。凡是这类快照式引用，都要单独覆盖一遍。
    try:
        import http.client as hc
        _patch(hc.HTTPConnection, "_create_connection", _create_connection)
        _patch(hc.HTTPSConnection, "_create_connection", _create_connection)
    except Exception:
        pass

    # ④ 让 urllib 无论环境变量怎么变都拿不到代理
    try:
        import urllib.request as ur
        _patch(ur, "getproxies", lambda: {})
        _patch(ur, "getproxies_environment", lambda: {})
        if hasattr(ur, "proxy_bypass_environment"):
            _patch(ur, "proxy_bypass_environment", lambda host, proxies=None: True)
        # ⑤ 清掉**已经构建好的**默认 opener。
        #    这是个真实的洞：`urllib.request` 会把 opener 缓存进 `_opener`，而
        #    `ProxyHandler` 在 `build_opener()` 时就把 `getproxies()` 的结果**快照**了。
        #    若在 engage() 之前已经有人 urlopen 过（例如语音客户端先探活），
        #    那份快照里就带着 HTTP_PROXY=127.0.0.1:7897 ——
        #    之后再摘环境变量、把 getproxies 换成空实现，都改不动这个已建好的 handler。
        #    结果：请求发给回环代理后仍可由代理转发到外网。置空后下次 urlopen
        #    会用打过补丁的 getproxies 重建。
        ur._opener = None
    except Exception:
        pass


def release():
    """还原（主要给单元测试用）。"""
    global _engaged
    while _patches:
        obj, name, orig = _patches.pop()
        setattr(obj, name, orig)
    for k, v in _saved_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    os.environ.pop("CIVENTHRA_PROCESS_NETWORK_GUARD", None)
    _saved_env.clear()
    _engaged = False
    # 对称地清一次缓存的 opener：否则它会一直用 engage 期间那份「无代理」的快照
    try:
        import urllib.request as ur
        ur._opener = None
    except Exception:
        pass


def engaged() -> bool:
    return _engaged


# ── 被替换掉的实现 ──────────────────────────────────────────────────────────

def _gai(host, *args, **kwargs):
    if not _is_local(host):
        raise AirgapError(
            "进程内出站限制：已拦截外部域名解析 %r" % (host,))
    return _real_gai(host, *args, **kwargs)


def _create_connection(address, *args, **kwargs):
    host = address[0] if isinstance(address, (tuple, list)) else address
    if not _is_local(host):
        raise AirgapError("进程内出站限制：已拦截外部连接 %r:%s" % (host, address[1] if isinstance(address, (tuple, list)) else "?"))
    return _real_create_connection(address, *args, **kwargs)


def _connect(self, address, *args, **kwargs):
    host = address[0] if isinstance(address, (tuple, list)) else address
    if not _is_local(host):
        raise AirgapError("进程内出站限制：已拦截外部连接 %r" % (host,))
    return _real_connect(self, address, *args, **kwargs)


def _connect_ex(self, address, *args, **kwargs):
    host = address[0] if isinstance(address, (tuple, list)) else address
    if not _is_local(host):
        return 111  # ECONNREFUSED，语义上等价于「网络不可达」
    return _real_connect_ex(self, address, *args, **kwargs)


def _sendto(self, data, *args, **kwargs):
    address = args[-1] if args else kwargs.get("address")
    host = address[0] if isinstance(address, (tuple, list)) else address
    if not _is_local(host):
        raise AirgapError("进程内出站限制：已拦截外部 UDP 发送 %r" % (host,))
    return _real_sendto(self, data, *args, **kwargs)


# 先保存原始实现（必须在 patch 之前执行）
_real_gai = socket.getaddrinfo
_real_create_connection = socket.create_connection
_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex
_real_sendto = socket.socket.sendto


# ── 自证 ────────────────────────────────────────────────────────────────────

def verify(verbose: bool = False) -> bool:
    """验证进程内钩子会拒绝外部目标；不访问真实网络。"""
    if not _engaged:
        raise RuntimeError("verify() 必须在 engage() 之后调用")
    probes = [
        ("DNS", lambda: socket.getaddrinfo("civenthra.invalid", 443)),
        ("TCP", lambda: socket.create_connection(("203.0.113.1", 9), timeout=0.01)),
    ]
    results = []
    for label, probe in probes:
        try:
            probe()
        except AirgapError:
            blocked = True
        except Exception:
            blocked = False
        else:
            blocked = False
        results.append(blocked)
        if verbose:
            print("    %-18s %s" % (label, "钩子生效" if blocked else "!! 钩子未生效"))
    return all(results)


class guard:
    """with guard(): ...  作用域内启用进程出站限制。"""

    def __enter__(self):
        self._was = _engaged
        engage()
        return self

    def __exit__(self, *exc):
        if not self._was:
            release()
        return False


def banner() -> str:
    return (
        "\n"
        "  ╔══════════════════════════════════════════════════╗\n"
        "  ║   🔥  进 程 出 站 限 制 已 激 活                 ║\n"
        "  ║   仅保护当前 Python 进程 · 不等同物理断网        ║\n"
        "  ╚══════════════════════════════════════════════════╝\n"
    )
