"""检索 SQL 的组装测试（不连数据库）。

钉住一个真实踩过的坑：过滤器片段是 **SQL 片段**，必须拼进 SQL 文本，
不能放进 params 当参数绑定。一旦绑定，服务器收到的是一个孤立的 `$n`，
报 "syntax error at or near $2"——而且过滤器为空时同样会炸，
所以这个 bug 在"不带任何过滤条件"的最常见场景下 100% 复现。
"""

from __future__ import annotations

import pytest

from app.infra import retrieval


class _FakeCursor:
    async def fetchall(self) -> list:
        return []


class _FakeConn:
    """只记录下发给驱动的 SQL 和参数，不真连库。"""

    def __init__(self) -> None:
        self.sql: str = ""
        self.params: dict = {}

    async def execute(self, sql: str, params: dict | None = None) -> _FakeCursor:
        self.sql = sql
        self.params = params or {}
        return _FakeCursor()


@pytest.mark.asyncio
async def test_filter_fragment_is_interpolated_not_bound() -> None:
    conn = _FakeConn()
    await retrieval.hybrid_search(conn, query_text="RLS", query_vec=[0.1, 0.2], kind="note")

    # 占位符必须已经被片段替换掉
    assert "%(vec_filter)s" not in conn.sql
    assert "%(kw_filter)s" not in conn.sql
    # 片段不能出现在参数里，否则就是被当成值绑定了
    assert "vec_filter" not in conn.params
    assert "kw_filter" not in conn.params
    # 片段内容确实进了 SQL，真正的值仍然是占位符
    assert "i.kind = %(f_kind)s" in conn.sql
    assert conn.params["f_kind"] == "note"


@pytest.mark.asyncio
async def test_empty_filter_leaves_no_dangling_placeholder() -> None:
    """最常见的场景：不带任何过滤。这里历史上有过 100% 复现的语法错误。"""
    conn = _FakeConn()
    await retrieval.hybrid_search(conn, query_text="RLS", query_vec=[0.1, 0.2])

    assert "%(vec_filter)s" not in conn.sql
    assert "%(kw_filter)s" not in conn.sql
    assert "vec_filter" not in conn.params
    # 日期/类型参数不该被塞进来
    assert "f_kind" not in conn.params


@pytest.mark.asyncio
async def test_keyword_only_path_binds_no_vector() -> None:
    """向量服务不可用时走纯关键词：不该出现向量占位符。"""
    conn = _FakeConn()
    await retrieval.hybrid_search(conn, query_text="RLS", query_vec=None)

    assert "%(vec)s" not in conn.sql
    assert "vec" not in conn.params
    assert "kw" in conn.params


@pytest.mark.asyncio
async def test_vector_path_binds_pgvector_literal() -> None:
    conn = _FakeConn()
    await retrieval.hybrid_search(conn, query_text="RLS", query_vec=[1.0, 0.0])

    assert conn.params["vec"] == "[1.000000,0.000000]"
    assert "%(vec)s::vector" in conn.sql


def test_build_filters_never_splices_user_input() -> None:
    """过滤器里的值一律走占位符，用户输入不会进 SQL 文本。"""
    params: dict = {}
    frag = retrieval.build_filters(kind="'; drop table items; --", params=params)
    assert "drop table" not in frag
    assert "%(f_kind)s" in frag
    assert params["f_kind"] == "'; drop table items; --"
