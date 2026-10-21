"""自检脚本自身的正确性。

verify_db.py 里手抄了一份表名和策略名清单，用来核对线上库。
抄错或 DDL 改动后忘了同步，后果是**校验假通过**——比不校验更危险。
所以这里直接解析迁移 SQL，断言两边完全一致。
"""

from __future__ import annotations

import re
from pathlib import Path

from scripts.verify_db import (
    EXPECTED_POLICIES,
    EXPECTED_TABLES,
    Report,
    _dsn,
    version_tuple,
)

SQL_PATH = Path(__file__).resolve().parents[2] / "migrations" / "001_init.sql"


def _sql() -> str:
    # 迁移文件是唯一事实来源，这里不做任何加工，避免测试跟着实现一起错
    return SQL_PATH.read_text(encoding="utf-8")


def _tables_in_sql() -> set[str]:
    return set(re.findall(r"create table if not exists (\w+)", _sql(), re.IGNORECASE))


def _policies_in_sql() -> dict[str, str]:
    """返回 {表名: 策略名}。DDL 里每张表只挂一个租户策略。"""
    pattern = re.compile(r"create policy (\w+) on (\w+)", re.IGNORECASE)
    return {table: policy for policy, table in pattern.findall(_sql())}


def test_expected_tables_match_migration() -> None:
    assert set(EXPECTED_TABLES) == _tables_in_sql()


def test_expected_policies_match_migration() -> None:
    actual = _policies_in_sql()
    assert set(EXPECTED_POLICIES) == set(actual), "策略清单与 DDL 不一致"
    for table, policy in EXPECTED_POLICIES.items():
        assert actual[table] == policy, f"{table} 的策略名应为 {actual[table]}"


def test_every_table_with_tenant_column_has_policy() -> None:
    """有 user_id 列的表必须有策略，否则那张表就是完全敞开的。

    三张表不用 user_id 隔离，各有理由，白名单写清楚免得被当成漏配：
      users            —— 租户列就是 id 本身，策略是 id = current_user_id()
      item_analyses    —— 子表，无 user_id，靠 exists 回溯 items
      digest_evidence  —— 子表，无 user_id，靠 exists 回溯 digests
    """
    indirect = {
        "users": "租户列是 id 本身",
        "item_analyses": "子表，回溯 items",
        "digest_evidence": "子表，回溯 digests",
    }
    # 明确不需要策略的表：全局队列没有租户维度，靠 grant 收紧而不是 RLS
    no_policy_needed = {"jobs": "全局队列，无租户列，靠 grant 收紧"}

    sql = _sql()
    for table in EXPECTED_TABLES:
        block = re.search(
            rf"create table if not exists {table} \((.*?)\n\);", sql, re.IGNORECASE | re.DOTALL
        )
        assert block, f"在 DDL 里找不到 {table} 的建表语句"

        if "user_id" in block.group(1):
            assert table in EXPECTED_POLICIES, f"{table} 有 user_id 却没有租户策略"
        elif table in EXPECTED_POLICIES:
            assert table in indirect, f"{table} 没有 user_id 却不在白名单里，策略可能漏配"
            assert indirect[table], f"{table} 缺白名单理由"
        else:
            assert table in no_policy_needed, f"{table} 既无策略也无白名单说明，可能漏配"
            # jobs 这类表必须没有策略，否则等于假装做了隔离
            assert table not in EXPECTED_POLICIES


def test_indirect_policies_reference_parent_or_self() -> None:
    """反向核对：白名单里的表和策略里写的列要对得上，防止策略抄了别的表。"""
    sql = _sql()
    assert re.search(
        r"create policy users_self on users\s+using \(id = current_user_id\(\)\)",
        sql,
        re.IGNORECASE,
    ), "users 的策略应该比对 id 而不是 user_id"
    assert re.search(
        r"using \(exists \(select 1 from items i where i\.id = item_analyses\.item_id\)\)",
        sql,
        re.IGNORECASE,
    ), "item_analyses 的策略应该回溯 items"
    assert re.search(
        r"using \(exists \(select 1 from digests d where d\.id = digest_evidence\.digest_id\)\)",
        sql,
        re.IGNORECASE,
    ), "digest_evidence 的策略应该回溯 digests"


def test_force_rls_only_where_sql_says_so() -> None:
    """force 清单必须和 DDL 一致：users 只 enable 不 force 是有意为之。"""
    forced = set(
        re.findall(r"alter table (\w+)\s+force\s+row level security", _sql(), re.IGNORECASE)
    )
    expected_forced = {t for t, mode in EXPECTED_TABLES.items() if mode == "force"}
    assert forced == expected_forced
    assert "users" not in forced, "users 加 force 会让登录函数被自己的策略挡住"


def test_jobs_is_the_only_table_without_rls() -> None:
    """jobs 是唯一预期不开 RLS 的表：全局队列，靠 grant 收紧。

    这条断言的价值：如果将来有人给 jobs 加了 enable rls 却没写策略，
    所有 worker 会静默地取不到任务。这里提前挡住。
    """
    no_rls = {t for t, mode in EXPECTED_TABLES.items() if mode == "none"}
    assert no_rls == {"jobs"}
    assert "jobs" not in EXPECTED_POLICIES
    # 不开 RLS 就必须靠显式 grant 收紧权限，不能只靠"没人会查"
    assert re.search(r"revoke all on jobs from pim_app", _sql(), re.IGNORECASE)
    assert re.search(r"grant insert on jobs to pim_app", _sql(), re.IGNORECASE)


def test_dsn_normalizes_driver_suffix() -> None:
    assert _dsn("postgresql+psycopg://a:b@h:5432/d") == "postgresql://a:b@h:5432/d"
    assert _dsn("postgres://a:b@h:5432/d") == "postgresql://a:b@h:5432/d"
    assert _dsn("postgresql://a:b@h:5432/d") == "postgresql://a:b@h:5432/d"


def test_version_tuple_handles_three_part_versions() -> None:
    """回归测试：pgvector 的 extversion 是 0.8.0。

    之前用 partition('.') 取主次版本，第二段拿到 '8.0'，int() 直接抛
    ValueError，脚本在真实库上第一次跑就崩了。
    """
    assert version_tuple("0.8.0") == (0, 8)
    assert version_tuple("0.5") == (0, 5)
    assert version_tuple("1") == (1, 0)
    assert version_tuple("0.8.0") >= (0, 5), "pgvector 0.8 必须被判定为支持 HNSW"
    assert version_tuple("0.4.4") < (0, 5), "0.4 不支持 HNSW，必须判为不通过"


def test_report_counts_and_exit_code() -> None:
    r = Report()
    r.pass_("ok")
    r.skipped("skip", "no db")
    assert (r.n_ok, r.n_fail, r.n_skip) == (1, 0, 1)
    assert r.summary() == 0

    r2 = Report()
    r2.fail("bad")
    assert r2.summary() == 1, "有失败项时必须返回非 0，否则 CI 会漏过"
