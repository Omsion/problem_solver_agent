# -*- coding: utf-8 -*-
"""
diag.py - 一键自检脚本

用途：在"推理结果不对/手机连不上/出答案太慢"时快速定位问题，而不是靠猜。

检查项：
1. 路径与配置（工作目录、监控目录、解答目录、图片参数）
2. API 密钥与求解器/视觉客户端可用性（是否只做了本地检查、是否真的调用）
3. Web 服务可达性（/api/health、/api/status）
4. 图片预处理收益实测（用真实截图跑一遍，报告体积缩小倍数）
5. 本机网络信息（局域网地址，供手机访问）

用法：
    python tools/diag.py                 # 只做本地检查
    python tools/diag.py --api           # 额外做一次真实 API 探活（会消耗极少额度）
    python tools/diag.py --port 8000     # 指定 Web 端口
    python tools/diag.py --image <路径>   # 指定用于预处理测试的图片
"""

from __future__ import annotations

import argparse
import glob
import json
import socket
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from problem_solver_agent import config  # noqa: E402
from problem_solver_agent.netcheck import get_lan_ip, local_addresses  # noqa: E402

OK = "[ OK ]"
WARN = "[WARN]"
FAIL = "[FAIL]"


def _line(mark: str, text: str) -> None:
    print(f"{mark} {text}")


def check_paths() -> None:
    print("\n== 路径与配置 ==")
    _line(OK if config._PROJECT_DIR.exists() else FAIL, f"项目目录    : {config._PROJECT_DIR}")
    _line(OK, f"工作根目录  : {config.ROOT_DIR}")
    _line(OK, f"截图监控目录: {config.MONITOR_DIR}")

    for label, path in (
        ("监控目录", config.MONITOR_DIR),
        ("归档目录", config.PROCESSED_DIR),
        ("解答目录", config.SOLUTION_DIR),
    ):
        if path.exists():
            _line(OK, f"{label}存在: {path}")
        else:
            _line(WARN, f"{label}尚不存在，首次运行时会自动创建: {path}")

    _line(
        OK,
        f"图片预处理  : 最长边 {config.IMAGE_MAX_EDGE} / JPEG q{config.IMAGE_JPEG_QUALITY} "
        f"/ 缓存={'开' if config.IMAGE_CACHE_ENABLED else '关'}",
    )
    _line(
        OK,
        f"合并视觉调用: {config.USE_COMBINED_VISION_CALL}"
        f"（auto 时仅 <= {config.COMBINED_VISION_MAX_IMAGES} 张图才尝试）",
    )
    _line(OK, f"并发上限    : {config.MAX_CONCURRENT_TASKS}")
    _line(
        OK,
        f"保留策略    : 最多 {config.TASK_RETENTION_COUNT} 个任务 / {config.TASK_RETENTION_DAYS} 天",
    )


def check_keys() -> bool:
    print("\n== API 密钥 ==")
    healthy = True
    if config.ZHIPU_API_KEY:
        _line(OK, "ZHIPU_API_KEY 已配置（视觉分类 / OCR / 视觉推理）")
    else:
        _line(FAIL, "ZHIPU_API_KEY 缺失：所有视觉步骤都会失败")
        healthy = False

    for provider in config.SOLVER_CONFIG:
        key = getattr(config, f"{provider.upper()}_API_KEY", None)
        if key:
            _line(OK, f"{provider.upper()}_API_KEY 已配置（求解器 {provider}）")
        else:
            _line(FAIL, f"{provider.upper()}_API_KEY 缺失：求解器 {provider} 不可用")
            healthy = False
    return healthy


def check_api_alive() -> None:
    print("\n== API 探活（会真实调用一次，消耗极少额度）==")
    from problem_solver_agent import solver_client, vision_client

    for provider, details in config.SOLVER_CONFIG.items():
        model = details["model"]
        try:
            ok = solver_client.check_solver_health(provider, model)
        except Exception as exc:
            _line(FAIL, f"求解器 {provider} 探活异常: {exc}")
            continue
        _line(OK if ok else FAIL, f"求解器 {provider} ({model}) {'可用' if ok else '不可用'}")

    try:
        client = vision_client._get_vision_client()  # noqa: SLF001
        _line(OK if client else FAIL, f"视觉客户端 {'初始化成功' if client else '初始化失败'}")
    except Exception as exc:
        _line(FAIL, f"视觉客户端初始化异常: {exc}")


def check_web(port: int) -> None:
    print(f"\n== Web 服务 (端口 {port}) ==")
    base = f"http://127.0.0.1:{port}"
    for path in ("/api/health", "/api/status"):
        try:
            with urllib.request.urlopen(f"{base}{path}", timeout=5) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            _line(OK, f"{path} 可达")
            if path == "/api/status":
                _line(
                    OK,
                    f"        自动导入={body.get('auto_import_enabled')} "
                    f"运行中={body.get('running')} 处理中={body.get('processing', 0)}",
                )
                uploads = body.get("uploads_bytes") or 0
                _line(OK, f"        上传目录占用 {uploads / 1024 / 1024:.1f} MB")
                _line(OK, f"        局域网地址 {body.get('lan_ip')}")
        except urllib.error.HTTPError as exc:
            _line(FAIL, f"{path} 返回 HTTP {exc.code}")
        except Exception as exc:
            _line(WARN, f"{path} 不可达（服务未启动？）: {exc}")


def check_image_prep(image: Path | None) -> None:
    print("\n== 图片预处理收益 ==")
    candidate = image
    if candidate is None:
        patterns = [
            str(config.MONITOR_DIR / "*.png"),
            str(config.MONITOR_DIR / "*.jpg"),
            "webapp/uploads/*/*.jpg",
            "webapp/uploads/*/*.png",
        ]
        found: list[str] = []
        for pattern in patterns:
            found.extend(glob.glob(pattern))
        if found:
            candidate = Path(found[0])

    if candidate is None or not candidate.exists():
        _line(WARN, "没找到可用图片，跳过（用 --image <路径> 指定）")
        return

    from problem_solver_agent.image_prep import prepare_for_api

    original = candidate.stat().st_size
    prepared = prepare_for_api(candidate)
    ratio = original / prepared.size_bytes if prepared.size_bytes else 0
    _line(OK, f"样本: {candidate.name}")
    _line(
        OK,
        f"      原图 {original / 1024 / 1024:.2f} MB -> 预处理 {prepared.size_bytes / 1024:.0f} KB "
        f"（缩小 {ratio:.1f} 倍）",
    )
    _line(OK, f"      输出 {prepared.width}x{prepared.height} {prepared.mime}")
    if ratio < 2:
        _line(WARN, "      缩小倍数偏低，图片可能本身已经很小")


def check_network() -> None:
    print("\n== 网络 ==")
    lan = get_lan_ip()
    _line(OK, f"局域网 IP: {lan}")
    _line(OK, f"手机访问地址: http://{lan}:8000")
    if lan.startswith("127."):
        _line(WARN, "未能探测到局域网 IP，手机可能无法访问；请检查网络连接")
    hostname = socket.gethostname()
    _line(OK, f"本机名: {hostname}")
    known = sorted(local_addresses())
    _line(OK, f"本机地址集合: {', '.join(known[:6])}{' ...' if len(known) > 6 else ''}")


def main() -> int:
    parser = argparse.ArgumentParser(description="项目自检")
    parser.add_argument("--api", action="store_true", help="额外做一次真实 API 探活")
    parser.add_argument("--port", type=int, default=8000, help="Web 服务端口")
    parser.add_argument("--image", type=str, default=None, help="用于预处理测试的图片路径")
    args = parser.parse_args()

    print("=" * 62)
    print("自动化多图解题 Agent —— 自检")
    print("=" * 62)

    check_paths()
    keys_ok = check_keys()
    check_network()
    check_web(args.port)
    check_image_prep(Path(args.image) if args.image else None)

    if args.api:
        check_api_alive()
    else:
        print("\n(跳过真实 API 探活；需要时加 --api)")

    print("\n" + "=" * 62)
    if keys_ok:
        print("结论：基础配置正常。若仍有问题，请看上方 WARN/FAIL 项。")
    else:
        print("结论：缺少必需密钥，请先补全 .env（可参考 .env.example）。")
    print("=" * 62)
    return 0 if keys_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
