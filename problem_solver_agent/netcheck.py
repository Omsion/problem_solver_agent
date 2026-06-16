"""
netcheck.py - 网络地址与客户端判定工具

抽成独立模块的原因：`webapp/routes.py` 原先用一段手写的字符串黑名单判断
"客户端是不是远程手机"：

    is_client_remote = client_host not in ("127.0.0.1", "::1", "localhost", lan_ip)

这是黑名单，凡是不在列表里的地址一律被当成手机。实际运行中，电脑端浏览器
自身的请求（例如二维码弹窗里的 <img src="/api/qrcode">）经常带着未列出的
本机地址，于是被误判为"手机已连接"，前端据此把"手机扫码"按钮隐藏掉。

本模块改为两条正面的判定条件，两者同时成立才算远程手机：

1. 来源 IP 不属于本机（回环段、链路本地、以及本机所有网卡地址都不算远程）
2. User-Agent 看起来是移动设备

只做标准库实现，便于在只读沙箱与单元测试中直接调用。
"""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
from typing import Iterable

logger = logging.getLogger(__name__)

# IP 字面量中不可能出现的字符（域名/主机名才有的字符）。
# 用于区分 "192.168.1.5"（IP）与 "my-pc.local"（主机名）。
_NON_IP_CHARS = re.compile(r"[^0-9a-fA-F:.]")

# 移动端 User-Agent 特征。仅用于把"这台电脑自己"排除在远程之外，
# 判定本身仍然以 IP 为准，User-Agent 只作为第二道过滤。
_MOBILE_UA_RE = re.compile(
    r"android|iphone|ipad|ipod|windows phone|mobile|harmonyos|micromessenger",
    re.IGNORECASE,
)

_CACHE: frozenset[str] | None = None


def _normalize(host: str | None) -> str | None:
    """去掉 IPv6 作用域后缀与方括号，返回可用于 ipaddress 解析的字符串。"""
    if not host:
        return None
    host = host.strip().strip("[]")
    if "%" in host:  # fe80::1%eth0
        host = host.split("%", 1)[0]
    return host or None


def local_addresses(refresh: bool = False) -> frozenset[str]:
    """返回本机所有可能出现在 request.client.host 里的地址字符串。

    包含回环地址、本机主机名解析出的地址，以及通过一个不实际发包的 UDP
    socket 探测到的出口地址（与 `_get_lan_ip` 取到的是同一个）。
    结果会缓存，避免每个请求都做一次 DNS 解析。
    """
    global _CACHE
    if _CACHE is not None and not refresh:
        return _CACHE

    found: set[str] = {"127.0.0.1", "::1", "localhost"}
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            found.add(info[4][0])
    except OSError:
        pass

    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.settimeout(0.5)
            probe.connect(("8.8.8.8", 80))
            found.add(probe.getsockname()[0])
        finally:
            probe.close()
    except OSError:
        pass

    _CACHE = frozenset(found)
    return _CACHE


def get_lan_ip(refresh: bool = False) -> str:
    """获取本机局域网 IP，失败则返回 127.0.0.1。

    优先从缓存的地址集合中挑一个非回环的 IPv4；没有则退回探测。
    """
    for addr in sorted(local_addresses(refresh)):
        try:
            parsed = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if parsed.version == 4 and not parsed.is_loopback:
            return addr

    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.settimeout(1)
            probe.connect(("8.8.8.8", 80))
            return probe.getsockname()[0]
        finally:
            probe.close()
    except OSError:
        return "127.0.0.1"


def is_local_client(host: str | None, extra_local: Iterable[str] = ()) -> bool:
    """判断来源地址是否属于本机。

    - 主机名、IPv4/IPv6 回环段、链路本地段一律视为本机
    - 命中 `local_addresses()` 或 `extra_local` 的显式地址串也视为本机
    - 无法解析的地址（空值、异常主机名）**不**视为本机以外的设备，
      避免把一个连地址都拿不到的请求当成手机连接
    """
    normalized = _normalize(host)
    if not normalized:
        return True
    if normalized in local_addresses() or normalized in set(extra_local):
        return True
    if _NON_IP_CHARS.search(normalized):
        # 不是 IP 字面量（例如 "localhost" 的其他写法），按本机处理
        return True

    try:
        parsed = ipaddress.ip_address(normalized)
    except ValueError:
        return True

    return parsed.is_loopback or parsed.is_link_local


def looks_like_mobile(user_agent: str | None) -> bool:
    """User-Agent 是否像移动设备。空值返回 False（保守：不认成手机）。"""
    if not user_agent:
        return False
    return bool(_MOBILE_UA_RE.search(user_agent))


def is_remote_device(
    host: str | None,
    user_agent: str | None,
    extra_local: Iterable[str] = (),
) -> bool:
    """综合判定：来源不是本机 **且** 是移动设备，才算远程手机。

    两条判定都是"正面条件"，因此电脑端浏览器（无论用 localhost、127.0.0.1
    还是局域网 IP 访问）永远不会被判成远程，二维码按钮也就不会自己消失。
    """
    if is_local_client(host, extra_local):
        return False
    return looks_like_mobile(user_agent)
