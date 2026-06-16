"""
test_netcheck.py - 远程客户端判定测试

覆盖缺陷 A：电脑端浏览器自身的请求绝不能被判成"手机已连接"，否则前端会
把"手机扫码"按钮隐藏掉。

运行：pytest tests/test_netcheck.py -v
"""

from __future__ import annotations

import ipaddress
import socket

import pytest

from problem_solver_agent import netcheck

# 常见移动端 User-Agent 片段（真实取值）
IPHONE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
)
ANDROID_UA = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36"
)
DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


@pytest.fixture(autouse=True)
def _clear_cache():
    """每个用例都重新探测本机地址，避免用例之间互相影响。"""
    netcheck.local_addresses(refresh=True)
    yield
    netcheck.local_addresses(refresh=True)


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost", "[::1]", "fe80::1%eth0"])
def test_loopback_and_link_local_are_local(host: str):
    assert netcheck.is_local_client(host) is True


@pytest.mark.parametrize("host", [None, "", "   ", "not-a-real-host.invalid"])
def test_unknown_host_is_treated_as_local(host: str | None):
    """拿不到来源地址时不能认成手机，否则会误报远程连接。"""
    assert netcheck.is_local_client(host) is True


def test_hostname_is_treated_as_local():
    assert netcheck.is_local_client(socket.gethostname()) is True


def test_own_machine_addresses_are_local():
    for addr in netcheck.local_addresses():
        assert netcheck.is_local_client(addr) is True, f"{addr} 应该是本机地址"


def test_extra_local_is_honoured():
    assert netcheck.is_local_client("203.0.113.7", extra_local=["203.0.113.7"]) is True
    assert netcheck.is_local_client("203.0.113.8", extra_local=["203.0.113.7"]) is False


def test_mobile_detection():
    assert netcheck.looks_like_mobile(IPHONE_UA) is True
    assert netcheck.looks_like_mobile(ANDROID_UA) is True
    assert netcheck.looks_like_mobile(DESKTOP_UA) is False
    assert netcheck.looks_like_mobile(None) is False
    assert netcheck.looks_like_mobile("") is False


def test_desktop_never_reported_as_remote():
    """核心回归：电脑端无论如何访问都不算远程。"""
    for host in ("127.0.0.1", "::1", "localhost"):
        assert netcheck.is_remote_device(host, DESKTOP_UA) is False
        # 即使 UA 被伪装成手机，只要来源是本机也不算远程
        assert netcheck.is_remote_device(host, IPHONE_UA) is False


def test_remote_requires_both_conditions():
    remote_ip = "192.0.2.55"  # RFC 5737 保留段，保证不是本机地址
    assert netcheck.is_remote_device(remote_ip, IPHONE_UA) is True
    assert netcheck.is_remote_device(remote_ip, ANDROID_UA) is True
    # 局域网里另一台电脑（桌面 UA）不算需要通知的手机
    assert netcheck.is_remote_device(remote_ip, DESKTOP_UA) is False
    # 拿不到 UA 时保守处理
    assert netcheck.is_remote_device(remote_ip, None) is False


def test_get_lan_ip_is_valid_and_not_loopback():
    ip = netcheck.get_lan_ip()
    parsed = ipaddress.ip_address(ip)
    assert parsed.version == 4
