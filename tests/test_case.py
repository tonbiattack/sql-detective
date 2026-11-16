"""Issue #4: 事件生成（一意解の逆算生成）のテスト。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from sql_detective.case import (
    TEMPLATES,
    TEMPLATES_BY_KEY,
    CaseGenerationError,
    generate_case,
    verify_case_db,
)
from sql_detective.db import TABLE_COLUMNS, TABLE_ORDER, ReadOnlyDatabase

VERIFY_SEEDS = range(1, 101)


# --- 受け入れ条件: 100件で一意解 -------------------------------------------


@pytest.mark.parametrize("seed", VERIFY_SEEDS)
def test_every_case_has_unique_solution(seed: int, tmp_path: Path) -> None:
    """生成後に検証クエリで一意性を確認できること（複数解の事件を出さない）。"""
    case = generate_case(seed)
    path = case.write_db(tmp_path / f"case-{seed}.db")
    matches = verify_case_db(path, case.template)
    assert matches == [case.culprit_id], (
        f"seed={seed} ({case.template.key}): 条件を満たすのは {matches}、"
        f"犯人は {case.culprit_id}"
    )


def test_all_templates_are_exercised() -> None:
    """100件の検証で全事件タイプが実際に出現すること。"""
    seen = {generate_case(seed).template.key for seed in VERIFY_SEEDS}
    assert seen == set(TEMPLATES_BY_KEY)


def test_generation_is_deterministic() -> None:
    a = generate_case(42)
    b = generate_case(42)
    assert a.template.key == b.template.key
    assert a.culprit_id == b.culprit_id
    assert a.tables == b.tables


def test_different_seeds_produce_different_data() -> None:
    a = generate_case(1)
    b = generate_case(2)
    assert a.tables != b.tables


# --- テーブル構成は固定 -----------------------------------------------------


def test_table_structure_is_fixed(tmp_path: Path) -> None:
    """列構成は固定で、データだけがシードで変わること。"""
    expected = {
        table: list(TABLE_COLUMNS[table]) for table in TABLE_ORDER
    }
    for seed in (1, 2, 3, 50, 99):
        path = generate_case(seed).write_db(tmp_path / f"s{seed}.db")
        conn = sqlite3.connect(path)
        try:
            for table, columns in expected.items():
                actual = [
                    row[1] for row in conn.execute(f"PRAGMA table_info({table})")
                ]
                assert actual == columns, f"seed={seed} {table}"
        finally:
            conn.close()


def test_all_four_tables_are_populated(tmp_path: Path) -> None:
    case = generate_case(11)
    path = case.write_db(tmp_path / "case.db")
    with ReadOnlyDatabase(path) as db:
        for table in TABLE_ORDER:
            count = db.execute(f"SELECT COUNT(*) FROM {table}").rows[0][0]
            assert count > 0, f"{table} が空"


# --- データの整合性 ---------------------------------------------------------


@pytest.mark.parametrize("seed", [1, 5, 17, 33, 64, 88])
def test_foreign_keys_resolve(seed: int, tmp_path: Path) -> None:
    """全参照が users に存在すること。"""
    case = generate_case(seed)
    path = case.write_db(tmp_path / "case.db")
    with ReadOnlyDatabase(path) as db:
        checks = {
            "orders": "SELECT COUNT(*) FROM orders o LEFT JOIN users u ON u.id = o.user_id WHERE u.id IS NULL",
            "logins": "SELECT COUNT(*) FROM logins l LEFT JOIN users u ON u.id = l.user_id WHERE u.id IS NULL",
            "transfers_from": "SELECT COUNT(*) FROM transfers t LEFT JOIN users u ON u.id = t.from_user_id WHERE u.id IS NULL",
            "transfers_to": "SELECT COUNT(*) FROM transfers t LEFT JOIN users u ON u.id = t.to_user_id WHERE u.id IS NULL",
        }
        for label, sql in checks.items():
            assert db.execute(sql).rows[0][0] == 0, f"seed={seed} {label}"


@pytest.mark.parametrize("seed", [1, 5, 17, 33, 64, 88])
def test_transfer_sender_is_not_receiver(seed: int, tmp_path: Path) -> None:
    case = generate_case(seed)
    path = case.write_db(tmp_path / "case.db")
    with ReadOnlyDatabase(path) as db:
        count = db.execute(
            "SELECT COUNT(*) FROM transfers WHERE from_user_id = to_user_id"
        ).rows[0][0]
    assert count == 0


def test_culprit_is_a_real_user(tmp_path: Path) -> None:
    for seed in VERIFY_SEEDS:
        case = generate_case(seed)
        ids = {int(row[0]) for row in case.users}
        assert case.culprit_id in ids
        assert case.user_name(case.culprit_id) == case.culprit_name


def test_admin_impersonation_culprit_is_not_admin() -> None:
    """管理者への送金事件の犯人は必ず非管理者であること。"""
    for seed in VERIFY_SEEDS:
        case = generate_case(seed)
        if case.template.key != "admin_impersonation":
            continue
        is_admin = next(
            int(row[4]) for row in case.users if int(row[0]) == case.culprit_id
        )
        assert is_admin == 0


# --- 告発の受け付け ---------------------------------------------------------


def test_accepts_answer_by_id_and_name() -> None:
    case = generate_case(3)
    assert case.accepts_answer(str(case.culprit_id))
    assert case.accepts_answer(case.culprit_name)
    assert case.accepts_answer(case.culprit_name.replace(" ", ""))
    assert case.accepts_answer(f"  {case.culprit_id}  ")


def test_rejects_wrong_answer() -> None:
    case = generate_case(3)
    other = next(
        int(row[0]) for row in case.users if int(row[0]) != case.culprit_id
    )
    assert not case.accepts_answer(str(other))
    assert not case.accepts_answer("")
    assert not case.accepts_answer("存在しない人")


# --- テンプレート定義 -------------------------------------------------------


def test_templates_have_required_fields() -> None:
    for template in TEMPLATES:
        assert template.key
        assert template.title
        assert template.brief
        assert template.hint
        assert "SELECT" in template.predicate_sql.upper()


def test_template_keys_are_unique() -> None:
    keys = [t.key for t in TEMPLATES]
    assert len(keys) == len(set(keys))


def test_generation_error_is_raised_when_impossible(monkeypatch) -> None:
    """一意解を作れない場合は例外にすること（黙って複数解を出さない）。"""
    import sql_detective.case as case_module

    monkeypatch.setattr(case_module, "verify_case_db", lambda *a, **k: [1, 2])
    with pytest.raises(CaseGenerationError):
        generate_case(1, max_attempts=3)
