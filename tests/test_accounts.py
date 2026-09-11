"""
test_accounts.py - 账户/密码/额度/用量/验证码测试

覆盖：
- 密码哈希：随机盐、`scrypt$<salt>$<hash>` 格式、非法输入不抛异常
- 用户 CRUD：按 id/手机号/密钥查询、分页计数、登录时间、内置用户幂等
- 安全视图：`to_public_dict()` 不泄露密码哈希、原始手机号与完整 API Key
- 额度：够用不抛、不足抛 BudgetExceededError（含 remaining/required）、消费后余量下降
- 用量：record_usage 费用与 estimate_cost 一致、按模型/按阶段聚合、全局 Top 用户排序
- 密钥：rotate_api_key 后旧 key 立即失效；set_budget/set_role 对不存在的用户返回 False
- 短信验证码：一次性消费、错误码拒绝、过期码拒绝（直接改库，不 sleep）

运行：pytest tests/test_accounts.py -v
"""

from __future__ import annotations

import json
import sqlite3
import time

import pytest

from webapp.accounts import (
    COST_TABLE,
    ROLE_ADMIN,
    ROLE_USER,
    AccountManager,
    BudgetExceededError,
    estimate_cost,
    generate_api_key,
    hash_password,
    mask_key,
    mask_phone,
    verify_password,
)


@pytest.fixture()
def manager(tmp_path) -> AccountManager:
    """每个测试独立临时数据库，绝不触碰真实的 webapp/data/tasks.db。"""
    return AccountManager(tmp_path / "accounts.db")


# ---------------------------------------------------------------------------
# 密码哈希
# ---------------------------------------------------------------------------


def test_hash_password_uses_random_salt():
    """同一密码两次调用必须得到不同哈希（盐随机），且都能校验通过。"""
    first = hash_password("same-password")
    second = hash_password("same-password")
    assert first != second
    assert verify_password("same-password", first) is True
    assert verify_password("same-password", second) is True


def test_hash_password_format():
    """哈希格式固定为 scrypt$<salt>$<hash>。"""
    stored = hash_password("pw-123456")
    assert stored.startswith("scrypt$")
    scheme, salt, digest = stored.split("$")
    assert scheme == "scrypt"
    assert len(salt) == 32  # secrets.token_hex(16)
    assert len(digest) == 64  # 32 字节转十六进制
    assert all(ch in "0123456789abcdef" for ch in salt + digest)


def test_verify_password_accepts_correct_and_rejects_wrong():
    stored = hash_password("correct horse")
    assert verify_password("correct horse", stored) is True
    assert verify_password("wrong horse", stored) is False
    assert verify_password("Correct horse", stored) is False


@pytest.mark.parametrize(
    "stored",
    [
        "garbage",  # 没有 $，split 直接失败
        "bcrypt$xx$yy",  # 算法不是 scrypt
        "scrypt$only-two",  # 字段数量不对
        "",
        None,
    ],
)
def test_verify_password_rejects_invalid_stored_values(stored):
    """格式非法/空哈希一律返回 False，绝不抛异常。"""
    assert verify_password("whatever", stored) is False


@pytest.mark.parametrize("password", ["", None])
def test_verify_password_rejects_empty_password(password):
    assert verify_password(password, hash_password("x")) is False


# ---------------------------------------------------------------------------
# 用户 CRUD
# ---------------------------------------------------------------------------


def test_create_user_is_queryable_by_id_phone_and_api_key(manager: AccountManager):
    user = manager.create_user(phone="13800000001", password="pw-123456")

    assert user.role == ROLE_USER
    assert user.tenant_id == "default"
    assert manager.get_user(user.id).id == user.id
    assert manager.get_user_by_phone("13800000001").id == user.id
    assert manager.get_user_by_api_key(user.api_key).id == user.id

    # 不存在的记录返回 None，而不是抛异常
    assert manager.get_user("u_not-exist") is None
    assert manager.get_user_by_phone("13800009999") is None
    assert manager.get_user_by_api_key("sk-solver-nope") is None
    assert manager.get_user_by_api_key("") is None


def test_new_user_remaining_equals_budget(manager: AccountManager):
    """未消费时 remaining 等于 budget。"""
    user = manager.create_user(phone="13800000002", password="pw", budget=12.5)
    assert user.spent == 0.0
    assert user.budget == pytest.approx(12.5)
    assert user.remaining == pytest.approx(12.5)
    assert user.last_login_at is None


def test_stored_password_is_hashed_not_plaintext(manager: AccountManager):
    user = manager.create_user(phone="13800000003", password="pw-123456")
    stored = manager.get_hashed_password(user.id)

    assert stored is not None
    assert stored != "pw-123456"
    assert stored.startswith("scrypt$")
    assert manager.verify_user_password(user.id, "pw-123456") is True
    assert manager.verify_user_password(user.id, "not-my-password") is False


def test_to_public_dict_hides_secrets(manager: AccountManager):
    """安全回归：公开视图不得包含密码哈希、原始手机号与完整 API Key。"""
    raw_phone = "13812345678"
    user = manager.create_user(phone=raw_phone, password="pw-123456", budget=5.0)
    public = user.to_public_dict()
    dumped = json.dumps(public, ensure_ascii=False)

    assert "hashed_password" not in public
    assert not any("password" in key for key in public)
    assert raw_phone not in dumped  # 原始手机号不得出现
    assert user.api_key not in dumped  # 完整密钥不得出现
    assert public["phone"] == mask_phone(raw_phone)
    assert public["api_key_masked"] == mask_key(user.api_key)
    assert "****" in public["phone"]
    assert "..." in public["api_key_masked"]


def test_list_users_pagination_and_count(manager: AccountManager):
    created = [manager.create_user(phone=f"1390000000{i}", password="pw") for i in range(3)]

    assert manager.count_users() == 3

    first_page = manager.list_users(limit=2, offset=0)
    second_page = manager.list_users(limit=2, offset=2)
    assert len(first_page) == 2
    assert len(second_page) == 1  # 第二页只剩 1 条
    # 分页不重叠且合起来正好是全部用户
    assert {u.id for u in first_page}.isdisjoint({u.id for u in second_page})
    assert {u.id for u in first_page} | {u.id for u in second_page} == {u.id for u in created}
    assert {u.id for u in manager.list_users(limit=100)} == {u.id for u in created}


def test_touch_login_updates_last_login_at(manager: AccountManager):
    user = manager.create_user(phone="13800000010", password="pw")
    assert user.last_login_at is None

    manager.touch_login(user.id)
    updated = manager.get_user(user.id)
    assert updated.last_login_at is not None
    assert updated.last_login_at > 0


def test_ensure_local_user_is_idempotent(manager: AccountManager):
    """内置本地用户：重复调用返回同一用户，不重复插入。"""
    first = manager.ensure_local_user("local", "default")
    second = manager.ensure_local_user("local", "default")

    assert first.id == second.id == "local"
    assert first.role == ROLE_ADMIN
    assert first.is_admin is True
    assert first.tenant_id == "default"
    assert manager.count_users() == 1  # 幂等：只有一行


# ---------------------------------------------------------------------------
# 额度
# ---------------------------------------------------------------------------


def test_check_budget_passes_when_enough(manager: AccountManager):
    user = manager.create_user(phone="13800000020", password="pw", budget=1.0)
    manager.check_budget(user.id, 0.5)  # 不应抛异常
    manager.check_budget(user.id, 1.0)  # 恰好够用


def test_check_budget_raises_with_details(manager: AccountManager):
    user = manager.create_user(phone="13800000021", password="pw", budget=0.3)

    with pytest.raises(BudgetExceededError) as excinfo:
        manager.check_budget(user.id, 0.8)

    assert excinfo.value.remaining == pytest.approx(0.3)
    assert excinfo.value.required == pytest.approx(0.8)
    assert "额度不足" in str(excinfo.value)


def test_check_budget_for_unknown_user(manager: AccountManager):
    with pytest.raises(BudgetExceededError) as excinfo:
        manager.check_budget("u_missing", 1.0)
    assert excinfo.value.remaining == 0.0
    assert excinfo.value.required == pytest.approx(1.0)


def test_spending_reduces_remaining(manager: AccountManager):
    """消费后 spent 增加、remaining 相应减少。"""
    user = manager.create_user(phone="13800000022", password="pw", budget=1.0)
    manager.record_usage(
        user_id=user.id,
        stage="solve",
        model="deepseek-v4-flash",
        input_tokens=1_000_000,
        output_tokens=0,
    )  # 0.5 元

    after = manager.get_user(user.id)
    assert after.spent == pytest.approx(0.5)
    assert after.remaining == pytest.approx(0.5)
    # 花掉一半后，需要 0.6 元的调用应被拒绝
    with pytest.raises(BudgetExceededError):
        manager.check_budget(user.id, 0.6)


# ---------------------------------------------------------------------------
# 用量
# ---------------------------------------------------------------------------


def test_record_usage_cost_matches_estimate(manager: AccountManager):
    user = manager.create_user(phone="13800000023", password="pw", budget=10.0)
    event = manager.record_usage(
        user_id=user.id,
        stage="ocr",
        provider="zhipu",
        model="GLM-4.6V",
        input_tokens=2000,
        output_tokens=1000,
        task_id="t_1",
    )

    assert event["cost"] == estimate_cost("GLM-4.6V", 2000, 1000)
    assert event["user_id"] == user.id
    assert event["stage"] == "ocr"
    assert event["provider"] == "zhipu"
    assert event["model"] == "GLM-4.6V"
    assert event["task_id"] == "t_1"
    assert event["id"].startswith("e_")

    # 流水确实落库，且用户累计消费同步增加
    assert [row["id"] for row in manager.list_usage(user.id)] == [event["id"]]
    assert manager.get_user(user.id).spent == pytest.approx(event["cost"])


def test_usage_summary_aggregates_by_model_and_stage(manager: AccountManager):
    user = manager.create_user(phone="13800000024", password="pw", budget=100.0)
    manager.record_usage(
        user_id=user.id, stage="ocr", model="deepseek-v4-flash",
        input_tokens=1_000_000, output_tokens=0,
    )  # 0.5
    manager.record_usage(
        user_id=user.id, stage="solve", model="deepseek-v4-pro",
        input_tokens=0, output_tokens=1_000_000,
    )  # 8.0
    manager.record_usage(
        user_id=user.id, stage="solve", model="deepseek-v4-flash",
        input_tokens=0, output_tokens=1_000_000,
    )  # 2.0

    summary = manager.usage_summary(user.id)
    assert summary["calls"] == 3
    assert summary["cost"] == pytest.approx(10.5)
    assert summary["input_tokens"] == 1_000_000
    assert summary["output_tokens"] == 2_000_000

    by_model = {row["model"]: row for row in summary["by_model"]}
    assert by_model["deepseek-v4-pro"]["calls"] == 1
    assert by_model["deepseek-v4-pro"]["cost"] == pytest.approx(8.0)
    assert by_model["deepseek-v4-flash"]["calls"] == 2
    assert by_model["deepseek-v4-flash"]["cost"] == pytest.approx(2.5)

    by_stage = {row["stage"]: row for row in summary["by_stage"]}
    assert by_stage["solve"]["calls"] == 2
    assert by_stage["solve"]["cost"] == pytest.approx(10.0)
    assert by_stage["ocr"]["calls"] == 1
    assert by_stage["ocr"]["cost"] == pytest.approx(0.5)

    # 两个维度的聚合都按花费倒序
    model_costs = [row["cost"] for row in summary["by_model"]]
    stage_costs = [row["cost"] for row in summary["by_stage"]]
    assert model_costs == sorted(model_costs, reverse=True)
    assert stage_costs == sorted(stage_costs, reverse=True)


def test_global_usage_summary_top_users_sorted_by_cost(manager: AccountManager):
    big = manager.create_user(phone="13800000030", password="pw", budget=100.0)
    small = manager.create_user(phone="13800000031", password="pw", budget=100.0)
    silent = manager.create_user(phone="13800000032", password="pw", budget=100.0)

    manager.record_usage(
        user_id=small.id, stage="ocr", model="deepseek-v4-flash",
        input_tokens=1_000_000, output_tokens=0,
    )  # 0.5
    manager.record_usage(
        user_id=big.id, stage="solve", model="deepseek-v4-pro",
        input_tokens=0, output_tokens=1_000_000,
    )  # 8.0

    summary = manager.global_usage_summary()
    assert summary["calls"] == 2
    assert summary["cost"] == pytest.approx(8.5)
    assert summary["output_tokens"] == 1_000_000

    ids = [row["id"] for row in summary["top_users"]]
    assert ids == [big.id, small.id, silent.id]  # 无消费用户排最后
    costs = [row["cost"] for row in summary["top_users"]]
    assert costs == sorted(costs, reverse=True)
    assert summary["top_users"][0]["calls"] == 1

    # 管理员看板同样不泄露原始手机号
    assert "13800000030" not in json.dumps(summary["top_users"])


def test_estimate_cost_unknown_model_uses_default_price():
    """未知模型回落到 COST_TABLE["default"] 单价（输入/输出分别取值）。"""
    default_input, default_output = COST_TABLE["default"]
    assert "no-such-model" not in COST_TABLE

    assert estimate_cost("no-such-model", 1_000_000, 0) == pytest.approx(default_input)
    assert estimate_cost("no-such-model", 0, 1_000_000) == pytest.approx(default_output)
    assert estimate_cost("no-such-model", 1_000_000, 1_000_000) == pytest.approx(
        default_input + default_output
    )


def test_estimate_cost_zero_tokens_is_free():
    assert estimate_cost("deepseek-v4-pro", 0, 0) == 0.0
    assert estimate_cost("no-such-model", 0, 0) == 0.0


# ---------------------------------------------------------------------------
# 密钥与角色/额度设置
# ---------------------------------------------------------------------------


def test_rotate_api_key_invalidates_old_key(manager: AccountManager):
    user = manager.create_user(phone="13800000040", password="pw")
    old_key = user.api_key
    assert old_key  # 注册时自动签发

    new_key = manager.rotate_api_key(user.id)
    assert new_key
    assert new_key != old_key
    assert new_key.startswith("sk-solver-")

    assert manager.get_user_by_api_key(old_key) is None  # 旧 key 立即失效
    assert manager.get_user_by_api_key(new_key).id == user.id


def test_rotate_api_key_for_unknown_user_returns_none(manager: AccountManager):
    assert manager.rotate_api_key("u_missing") is None


def test_set_budget_and_set_role_return_false_for_unknown_user(manager: AccountManager):
    assert manager.set_budget("u_missing", 1.0) is False
    assert manager.set_role("u_missing", ROLE_ADMIN) is False


def test_set_budget_and_set_role_apply_to_existing_user(manager: AccountManager):
    user = manager.create_user(phone="13800000041", password="pw")
    assert manager.set_budget(user.id, 3.5) is True
    assert manager.set_role(user.id, ROLE_ADMIN) is True

    updated = manager.get_user(user.id)
    assert updated.budget == pytest.approx(3.5)
    assert updated.role == ROLE_ADMIN
    assert updated.is_admin is True


def test_generate_api_key_is_prefixed_and_unique():
    first = generate_api_key()
    second = generate_api_key()
    assert first.startswith("sk-solver-")
    assert first != second


@pytest.mark.parametrize(
    "phone,expected",
    [
        ("13812345678", "138****5678"),
        ("123456", "1***"),  # 短号码只保留首位
        ("", ""),
        (None, ""),
    ],
)
def test_mask_phone(phone, expected):
    assert mask_phone(phone) == expected


@pytest.mark.parametrize(
    "key,expected",
    [
        ("sk-solver-abcdefghijklmnop", "sk-solv...mnop"),
        ("short", "shor***"),  # 短密钥只保留前 4 位
        ("", ""),
        (None, ""),
    ],
)
def test_mask_key(key, expected):
    assert mask_key(key) == expected


# ---------------------------------------------------------------------------
# 短信验证码
# ---------------------------------------------------------------------------


def test_save_and_verify_sms_code(manager: AccountManager):
    manager.save_sms_code("13900000001", "123456", ttl=300)
    assert manager.verify_sms_code("13900000001", "123456") is True


def test_verify_sms_code_rejects_wrong_code(manager: AccountManager):
    manager.save_sms_code("13900000002", "123456", ttl=300)

    assert manager.verify_sms_code("13900000002", "654321") is False
    # 校验失败不消费验证码，正确码仍可用
    assert manager.verify_sms_code("13900000002", "123456") is True


def test_sms_code_is_single_use(manager: AccountManager):
    manager.save_sms_code("13900000003", "123456", ttl=300)

    assert manager.verify_sms_code("13900000003", "123456") is True
    assert manager.verify_sms_code("13900000003", "123456") is False  # 已消费


def test_expired_sms_code_is_rejected(manager: AccountManager):
    phone = "13900000004"
    manager.save_sms_code(phone, "123456", ttl=300)

    # 直接把过期时间改到过去，避免用 sleep 拖慢整个套件
    with sqlite3.connect(str(manager.db_path)) as conn:
        conn.execute(
            "UPDATE sms_codes SET expires_at = ? WHERE phone = ?",
            (time.time() - 10, phone),
        )
        conn.commit()

    assert manager.verify_sms_code(phone, "123456") is False

    # 过期记录应被顺手清理
    with sqlite3.connect(str(manager.db_path)) as conn:
        left = conn.execute(
            "SELECT COUNT(*) FROM sms_codes WHERE phone = ?", (phone,)
        ).fetchone()[0]
    assert left == 0


def test_verify_sms_code_for_unknown_phone(manager: AccountManager):
    assert manager.verify_sms_code("13900000005", "123456") is False
