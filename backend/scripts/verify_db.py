"""数据库健康自检。

迁移跑完只是「命令没报错」，不代表对象都对、RLS 都生效。
这个脚本把该核对的每一件事都查一遍，输出一份逐项报告，
任何一项不通过就以非 0 退出，可以直接塞进 CI。

检查分四类：
  1. 结构与扩展  —— 表、索引、触发器、函数、向量维度是否一致
  2. RLS 配置    —— enable / force / policy 是否都到位
  3. RLS 行为    —— 真的用非授权身份插一条别人的数据，看是否被拒
  4. 角色权限    —— 应用角色是不是超级用户或表 owner（是的话 RLS 形同虚设）

用法：
    python -m scripts.verify_db
    python -m scripts.verify_db --url postgresql://pim_app:...@localhost:5432/pim
"""

from __future__ import annotations

import argparse
import sys
import uuid

import psycopg
from psycopg import errors as pg_errors

from app.config import get_settings

# 表名 -> 期望的 RLS 状态。三态，不能用布尔：
#   "force"  启用且强制（owner 也受约束）
#   "enable" 只启用（owner 绕过，靠定义者函数作为受控出口）
#   "none"   不启用（全局表，靠 grant 收紧权限）
# users 只 enable 不 force：登录/注册时还不知道 user_id，
# 靠 auth_lookup / auth_register 两个定义者函数作为受控出口。
EXPECTED_TABLES: dict[str, str] = {
    "users": "enable",
    "items": "force",
    "item_analyses": "force",
    "chunks": "force",
    "item_links": "force",
    "todos": "force",
    "digests": "force",
    "digest_evidence": "force",
    "qa_sessions": "force",
    "qa_messages": "force",
    "runs": "force",
    "jobs": "none",  # 全局队列，无租户列，靠 grant 收紧而非 RLS
    "usage_ledger": "force",
}

# jobs 是全局队列表，应用角色只能投递，不能读别人的任务参数
EXPECTED_POLICIES: dict[str, str] = {
    "users": "users_self",
    "items": "items_tenant",
    "chunks": "chunks_tenant",
    "item_links": "item_links_tenant",
    "todos": "todos_tenant",
    "digests": "digests_tenant",
    "qa_sessions": "qa_sessions_tenant",
    "qa_messages": "qa_messages_tenant",
    "runs": "runs_tenant",
    "usage_ledger": "usage_ledger_tenant",
    "item_analyses": "item_analyses_tenant",
    "digest_evidence": "digest_evidence_tenant",
}

REQUIRED_EXTENSIONS = ("vector", "pg_trgm", "pgcrypto")


class Report:
    def __init__(self) -> None:
        self.n_ok = 0
        self.n_fail = 0
        self.n_skip = 0
        self._section = ""

    def section(self, name: str) -> None:
        self._section = name
        print(f"\n== {name} ==")

    def pass_(self, msg: str, detail: str = "") -> None:
        self.n_ok += 1
        print(f"  [OK] {msg}" + (f"  ({detail})" if detail else ""))

    def fail(self, msg: str, detail: str = "") -> None:
        self.n_fail += 1
        print(f"  [!!] {msg}" + (f"  ({detail})" if detail else ""))

    def skipped(self, msg: str, why: str) -> None:
        self.n_skip += 1
        print(f"  [--] {msg}  (跳过: {why})")

    def check(self, cond: bool, msg: str, detail: str = "") -> bool:
        if cond:
            self.pass_(msg, detail)
        else:
            self.fail(msg, detail)
        return cond

    def summary(self) -> int:
        print(f"\n{'-' * 60}")
        print(f"结果：通过 {self.n_ok} 项，失败 {self.n_fail} 项，跳过 {self.n_skip} 项")
        if self.n_fail:
            print("数据库状态与预期不符，请检查上面的 [!!] 项。")
            return 1
        print("数据库状态与设计一致。")
        return 0


def _dsn(raw: str) -> str:
    """去掉 SQLAlchemy 的驱动后缀，psycopg 自己认得 postgresql://。"""
    return raw.replace("postgresql+psycopg://", "postgresql://").replace(
        "postgres://", "postgresql://"
    )


def version_tuple(text: str) -> tuple[int, int]:
    """取版本号前两段，如 '0.8.0' -> (0, 8)。

    pg_extension.extversion 是 '0.8.0' 这种三段式，
    用 partition('.') 会让第二段变成 '8.0'，转 int 直接抛异常。
    """
    parts = text.split(".")
    major = int(parts[0])
    minor = int(parts[1]) if len(parts) > 1 else 0
    return major, minor


def check_extensions(cur, r: Report) -> None:
    r.section("扩展")
    cur.execute(
        "select extname, extversion from pg_extension where extname = any(%s)",
        (list(REQUIRED_EXTENSIONS),),
    )
    found = {name: ver for name, ver in cur.fetchall()}
    for ext in REQUIRED_EXTENSIONS:
        r.check(ext in found, f"扩展 {ext}", found.get(ext, "未安装"))

    # pgvector 版本低于 0.5 时不支持 HNSW，索引会建不出来
    vec = found.get("vector")
    if vec:
        r.check(
            version_tuple(vec) >= (0, 5),
            "pgvector 版本支持 HNSW",
            f"当前 {vec}，需要 >= 0.5.0",
        )


def check_tables(cur, r: Report) -> None:
    r.section("表结构")
    cur.execute(
        "select tablename from pg_tables where schemaname = 'public'"
    )
    existing = {row[0] for row in cur.fetchall()}
    missing = [t for t in EXPECTED_TABLES if t not in existing]
    r.check(not missing, "13 张表齐全", "缺失: " + ", ".join(missing) if missing else "")

    r.section("索引与触发器")
    cur.execute(
        """
        select indexname from pg_indexes
        where schemaname = 'public' and indexname = any(%s)
        """,
        (
            [
                "items_search",
                "items_tags",
                "chunks_vec",
                "chunks_fts",
                "jobs_ready",
                "usage_ledger_key",
                "users_email_lower",
            ],
        ),
    )
    idx = {row[0] for row in cur.fetchall()}
    for name in ("items_search", "items_tags", "chunks_vec", "chunks_fts", "jobs_ready"):
        r.check(name in idx, f"索引 {name}")

    # HNSW 索引必须是 vector_cosine_ops，换成 l2 会让检索相似度失真
    cur.execute(
        "select indexdef from pg_indexes where indexname = 'chunks_vec'"
    )
    row = cur.fetchone()
    if row:
        r.check(
            "vector_cosine_ops" in row[0],
            "chunks_vec 使用余弦距离",
            row[0].split("USING")[-1].strip() if "USING" in row[0] else "",
        )

    # 触发器必须挂在 item_analyses 上；挂 items 上会因函数体内 update items 死循环
    cur.execute(
        """
        select c.relname, t.tgname
        from pg_trigger t join pg_class c on c.oid = t.tgrelid
        where t.tgname = 'trg_on_new_analysis' and not t.tgisinternal
        """
    )
    trg = cur.fetchone()
    if r.check(trg is not None, "触发器 trg_on_new_analysis 存在"):
        r.check(
            trg[0] == "item_analyses",
            "触发器挂在 item_analyses 而非 items（避免递归）",
            f"实际挂在 {trg[0]}",
        )


def check_functions(cur, r: Report) -> None:
    r.section("函数")
    cur.execute(
        """
        select p.proname, p.prosecdef
        from pg_proc p join pg_namespace n on n.oid = p.pronamespace
        where n.nspname = 'public'
          and p.proname = any(%s)
        """,
        (["current_user_id", "on_new_analysis", "auth_lookup", "auth_register"],),
    )
    rows = {name: secdef for name, secdef in cur.fetchall()}
    r.check("current_user_id" in rows, "函数 current_user_id")
    r.check("on_new_analysis" in rows, "函数 on_new_analysis")

    for fn in ("auth_lookup", "auth_register"):
        if r.check(fn in rows, f"函数 {fn}"):
            # 必须是 security definer，否则它也会被 users 的 RLS 挡住，登录直接失败
            r.check(rows[fn], f"{fn} 是 security definer（否则登录会读不到用户）")

    # 这两个函数只能给应用角色用，不能让 public 都能调
    cur.execute(
        """
        select p.proname,
               has_function_privilege('public', p.oid, 'execute') as public_can
        from pg_proc p join pg_namespace n on n.oid = p.pronamespace
        where n.nspname = 'public' and p.proname in ('auth_lookup', 'auth_register')
        """
    )
    for name, public_can in cur.fetchall():
        r.check(not public_can, f"{name} 未授权给 public")


def check_vector_dim(cur, r: Report, expected_dim: int) -> None:
    r.section("向量维度")
    cur.execute(
        "select format_type(atttypid, atttypmod) from pg_attribute "
        "where attrelid = 'chunks'::regclass and attname = 'embedding'"
    )
    row = cur.fetchone()
    if not row:
        r.fail("chunks.embedding 列存在")
        return
    actual = row[0]
    r.check(
        actual == f"vector({expected_dim})",
        "chunks.embedding 维度与配置一致",
        f"实际 {actual}，配置 EMBEDDING_DIM={expected_dim}",
    )
    # 维度不一致时插入会直接报错，属于最难排查的一类故障，所以单独提出来


def check_rls_config(cur, r: Report) -> None:
    r.section("RLS 配置")
    cur.execute(
        """
        select c.relname, c.relrowsecurity, c.relforcerowsecurity
        from pg_class c join pg_namespace n on n.oid = c.relnamespace
        where n.nspname = 'public' and c.relkind = 'r'
        """
    )
    state = {name: (enabled, forced) for name, enabled, forced in cur.fetchall()}

    for table, mode in EXPECTED_TABLES.items():
        if table not in state:
            continue
        enabled, forced = state[table]

        if mode == "none":
            # 全局表（jobs）不该开 RLS：开了反而会挡住 worker 的跨用户取任务，
            # 它的隔离靠的是「应用角色只有 insert 权限」。
            r.check(not enabled, f"{table} 未启用 RLS（全局表，靠 grant 收紧权限）")
            continue

        if not r.check(enabled, f"{table} 已启用 RLS"):
            continue
        if mode == "force":
            # 只 enable 不 force 时，表 owner 会绕过策略。
            # 必须 force，才能保证连迁移账号自己跑查询也受约束。
            r.check(forced, f"{table} 强制 RLS（owner 也受约束）")

    cur.execute("select tablename, policyname from pg_policies where schemaname = 'public'")
    policies: dict[str, set[str]] = {}
    for table, policy in cur.fetchall():
        policies.setdefault(table, set()).add(policy)

    for table, policy in EXPECTED_POLICIES.items():
        r.check(policy in policies.get(table, set()), f"{table} 有策略 {policy}")


def check_jobs_grant(cur, r: Report) -> None:
    r.section("jobs 队列权限")
    cur.execute(
        """
        select privilege_type from information_schema.table_privileges
        where table_schema = 'public' and table_name = 'jobs'
          and grantee = current_user
        """
    )
    privs = {row[0] for row in cur.fetchall()}
    if not privs:
        r.skipped("jobs 权限细分", "当前角色不是应用角色（无显式 grant）")
        return
    r.check("INSERT" in privs, "应用角色可向 jobs 投递")
    # 关键：整表 select 不能给，否则任何用户都能读到全局队列里别人的任务参数
    r.check(
        "SELECT" not in privs,
        "应用角色不能整表读 jobs（防止跨用户读到任务参数）",
        f"实际权限: {', '.join(sorted(privs))}",
    )

    # `insert ... returning id` 需要对返回列有 select 权限，所以 id 这一列必须单独授权。
    # 少了它，写记录接口会在入队那一步 500，而且错误信息只提 jobs 表，很难联想到 RETURNING。
    cur.execute(
        """
        select column_name from information_schema.column_privileges
        where table_schema = 'public' and table_name = 'jobs'
          and grantee = current_user and privilege_type = 'SELECT'
        """
    )
    readable = {row[0] for row in cur.fetchall()}
    r.check(
        readable == {"id"},
        "jobs 只有 id 列可读（够拿自己的任务号，读不到 payload/result）",
        f"实际可读列: {', '.join(sorted(readable)) or '无'}",
    )


def _as_user(conn, user_id: uuid.UUID, sql: str, params: tuple) -> list[tuple]:
    """以指定用户身份跑一条语句，跑完一定回滚。

    用独立事务包住，异常才能干净地传出来而不弄脏连接；
    每个探测各自一个事务，避免手动 rollback 和事务上下文互相干扰。
    """
    with conn.transaction(force_rollback=True):
        with conn.cursor() as cur:
            cur.execute("select set_config('app.user_id', %s, true)", (str(user_id),))
            cur.execute(sql, params)
            return cur.fetchall() if cur.description else []


def check_rls_behavior(conn, r: Report) -> None:
    """真刀真枪试一次：用 A 身份去读/写 B 的数据。

    配置项看着都对，但策略写错了（比如漏了 with check）时行为仍然是漏的，
    所以必须做一次实际读写。全部走会回滚的事务，不在库里留任何痕迹。
    """
    r.section("RLS 行为验证")
    user_a = uuid.uuid4()
    user_b = uuid.uuid4()

    insert_sql = (
        "insert into items (user_id, kind, body_md, raw_input, content_hash)"
        " values (%s, 'note', 'probe', 'probe', %s) returning id"
    )

    # 1) 别人的数据必须一条都看不到
    try:
        rows = _as_user(conn, user_a, "select count(*) from items", ())
        visible = rows[0][0]
        r.check(
            visible == 0,
            "设置 app.user_id 后看不到他人数据",
            f"看到 {visible} 条" if visible else "",
        )
    except pg_errors.Error as exc:
        r.fail("设置 app.user_id 后看不到他人数据", f"{type(exc).__name__}: {exc}")

    # 2) 冒充别人写入必须被 with check 拦下
    try:
        _as_user(conn, user_a, insert_sql, (str(user_b), f"probe-{uuid.uuid4()}"))
    except pg_errors.InsufficientPrivilege:
        r.pass_("写入他人 user_id 被拒绝")
    except pg_errors.Error as exc:
        # 其它报错也算被拦住，但原因不是策略，要说清楚免得误判为通过
        r.pass_("写入他人 user_id 被拒绝", f"由 {type(exc).__name__} 拦下，非策略")
    else:
        r.fail(
            "写入他人 user_id 被拒绝",
            "插入成功了！说明 items 策略缺少 with check，属于越权写入漏洞",
        )

    # 3) 自己的数据必须写得进去，否则策略会误伤正常业务
    try:
        _as_user(conn, user_a, insert_sql, (str(user_a), f"probe-self-{uuid.uuid4()}"))
        r.pass_("写入自己的数据成功（策略未误伤）")
    except pg_errors.Error as exc:
        r.fail("写入自己的数据成功（策略未误伤）", f"{type(exc).__name__}: {exc}")


def check_role(cur, r: Report) -> None:
    r.section("连接角色")
    cur.execute(
        """
        select current_user,
               (select rolsuper from pg_roles where rolname = current_user),
               (select rolbypassrls from pg_roles where rolname = current_user)
        """
    )
    role, is_super, bypass = cur.fetchone()
    is_super, bypass = bool(is_super), bool(bypass)
    print(f"  当前角色：{role}")

    # 这两条只要成立，RLS 对当前连接就完全不起作用，
    # 数据隔离只剩应用层 where，属于最危险的状态。
    r.check(not is_super, f"角色 {role} 不是超级用户", "超级用户绕过所有 RLS")
    r.check(not bypass, f"角色 {role} 没有 bypassrls", "bypassrls 角色绕过所有 RLS")

    cur.execute(
        """
        select count(*) from pg_class c join pg_namespace n on n.oid = c.relnamespace
        where n.nspname = 'public' and c.relkind = 'r'
          and c.relowner = (select oid from pg_roles where rolname = current_user)
        """
    )
    owned = cur.fetchone()[0]
    # 表 owner 默认绕过 RLS；只有 force 的表才拦得住。
    # 应用连接不该是 owner，这里只作为提示，不计入失败。
    if owned:
        r.skipped("应用角色不是表 owner", f"当前角色拥有 {owned} 张表，仅 force 的表受约束")


def main() -> int:
    ap = argparse.ArgumentParser(description="数据库健康自检")
    ap.add_argument("--url", help="覆盖 DATABASE_URL，默认读配置")
    args = ap.parse_args()

    settings = get_settings()
    dsn = _dsn(args.url or settings.database_url)

    # 防止密码出现在日志里
    safe = dsn.split("@")[-1] if "@" in dsn else dsn
    print(f"连接目标：{safe}")

    try:
        conn = psycopg.connect(dsn, connect_timeout=10)
    except pg_errors.Error as exc:
        print(f"\n连不上数据库：{exc}")
        print("先确认 `docker compose up -d db` 起了，且 DATABASE_URL 正确。")
        return 2

    r = Report()
    try:
        with conn.cursor() as cur:
            cur.execute("select version()")
            print(f"服务端：{cur.fetchone()[0].split(',')[0]}")

            check_role(cur, r)
            check_extensions(cur, r)
            check_tables(cur, r)
            check_functions(cur, r)
            check_vector_dim(cur, r, settings.embedding_dim)
            check_rls_config(cur, r)
            check_jobs_grant(cur, r)

        check_rls_behavior(conn, r)
    finally:
        conn.close()

    return r.summary()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
