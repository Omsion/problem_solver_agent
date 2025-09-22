#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
debug_api.py - API调试脚本
"""

import sys
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

import config
from deepseek_client import check_deepseek_health, ask_deepseek_for_analysis
from qwen_client import classify_problem_type, transcribe_images
from utils import setup_logger

logger = setup_logger()


def test_deepseek_api():
    """测试DeepSeek API"""
    print("\n" + "=" * 50)
    print("测试 DeepSeek API")
    print("=" * 50)

    if not config.DEEPSEEK_API_KEY:
        print("❌ DeepSeek API密钥未配置")
        return False

    print("🔍 检查API健康状态...")
    if check_deepseek_health():
        print("✅ DeepSeek API健康检查通过")

        # 测试简单请求
        print("🔍 发送测试请求...")
        test_prompt = "请用一句话介绍你自己"
        result = ask_deepseek_for_analysis(test_prompt, "{transcribed_text}")

        if result:
            print("✅ DeepSeek API测试成功")
            print(f"响应: {result[:100]}...")
            return True
        else:
            print("❌ DeepSeek API请求失败")
            return False
    else:
        print("❌ DeepSeek API健康检查失败")
        return False


def test_qwen_api():
    """测试Qwen API"""
    print("\n" + "=" * 50)
    print("测试 Qwen-VL API")
    print("=" * 50)

    if not config.DASHSCOPE_API_KEY:
        print("❌ Qwen-VL API密钥未配置")
        return False

    # 检查是否有测试图片
    test_images = list(config.MONITOR_DIR.glob("*.png"))
    if not test_images:
        print("⚠️  监控目录中没有找到测试图片")
        return True  # 没有图片不算API失败

    test_image = test_images[0]
    print(f"🔍 使用图片进行测试: {test_image.name}")

    try:
        # 测试分类功能
        result = classify_problem_type([test_image])
        if result:
            print(f"✅ Qwen-VL API测试成功 - 分类结果: {result}")
            return True
        else:
            print("❌ Qwen-VL API分类测试失败")
            return False
    except Exception as e:
        print(f"❌ Qwen-VL API测试异常: {e}")
        return False


def main():
    """主测试函数"""
    print("开始API调试...")

    # 初始化目录
    try:
        config.initialize_directories()
        print("✅ 目录初始化成功")
    except Exception as e:
        print(f"❌ 目录初始化失败: {e}")
        return

    # 测试API
    qwen_ok = test_qwen_api()
    deepseek_ok = test_deepseek_api()

    print("\n" + "=" * 50)
    print("调试结果总结")
    print("=" * 50)

    if qwen_ok and deepseek_ok:
        print("🎉 所有API测试通过！")
        print("建议：现在可以运行 main.py 进行完整测试")
    else:
        print("⚠️  部分API测试失败")
        if not qwen_ok:
            print("- 请检查 DASHSCOPE_API_KEY 配置")
        if not deepseek_ok:
            print("- 请检查 DEEPSEEK_API_KEY 配置和网络连接")

    print("\n调试完成。")


if __name__ == "__main__":
    main()