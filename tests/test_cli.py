"""Issue #5: CLI（最小プレイ可能UI）と事件検証のテスト。"""

from __future__ import annotations

import io
from contextlib import redirect_stdout

import pytest

from sql_detective.cli import build_parser, main


def run_cli(argv: list[str], stdin: str = "") -> tuple[int, str]:
    """CLI を実行し (終了コード, 標準出力) を返す。"""
    import builtins

    buffer = io.StringIO()
    original_input = builtins.input
    lines = iter(stdin.splitlines())

    def fake_input(prompt: str = "") -> str:
        buffer.write(prompt)
        try:
            value = next(lines)
        except StopIteration:
            raise EOFError from None
        buffer.write(value + "\n")
        return value

    builtins.input = fake_input
    try:
        with redirect_stdout(buffer):
            code = main(argv)
    finally:
        builtins.input = original_input
    return code, buffer.getvalue()


# --- パーサ -----------------------------------------------------------------


def test_parser_has_play_and_verify() -> None:
    parser = build_parser()
    assert parser.parse_args(["play"]).command == "play"
    assert parser.parse_args(["verify"]).command == "verify"


def test_parser_default_verify_count() -> None:
    assert build_parser().parse_args(["verify"]).count == 100


def test_no_command_prints_help() -> None:
    code, out = run_cli([])
    assert code == 0
    assert "sql-detective" in out


# --- verify -----------------------------------------------------------------


def test_verify_passes_for_small_count() -> None:
    code, out = run_cli(["verify", "--count", "10"])
    assert code == 0
    assert "すべてで解が一意" in out


def test_verify_reports_template_distribution() -> None:
    code, out = run_cli(["verify", "--count", "20"])
    assert code == 0
    assert "事件タイプの分布" in out


def test_verify_fails_when_uniqueness_breaks(monkeypatch) -> None:
    """一意性が壊れたら非ゼロ終了すること。"""
    import sql_detective.cli as cli_module

    monkeypatch.setattr(cli_module, "verify_case_db", lambda *a, **k: [1, 2])
    code, out = run_cli(["verify", "--count", "3"])
    assert code == 1
    assert "一意でない" in out


# --- play: 通しプレイ -------------------------------------------------------


def test_play_win_flow() -> None:
    """依頼→捜査→告発（正解）まで通しで動くこと。"""
    from sql_detective.case import generate_case

    case = generate_case(3)
    stdin = "\n".join(
        [
            "SELECT * FROM users LIMIT 3",
            "accuse",
            str(case.culprit_id),
        ]
    )
    code, out = run_cli(["play", "--seed", "3"], stdin)
    assert code == 0
    assert "【依頼】" in out
    assert "【解決】" in out
    assert "スコア:" in out


def test_play_shows_brief_and_schema() -> None:
    code, out = run_cli(["play", "--seed", "3"], "")
    assert code == 0
    assert "【依頼】" in out
    assert "users" in out and "transfers" in out
    assert "SELECT のみ実行できます" in out


def test_play_wrong_accusation_loses() -> None:
    from sql_detective.case import generate_case

    case = generate_case(3)
    other = next(
        int(row[0]) for row in case.users if int(row[0]) != case.culprit_id
    )
    code, out = run_cli(["play", "--seed", "3"], f"accuse\n{other}")
    assert code == 0
    assert "【敗北】" in out
    assert "真犯人" in out


def test_play_rejects_non_select() -> None:
    code, out = run_cli(
        ["play", "--seed", "3"],
        "DELETE FROM users\nSELECT * FROM users LIMIT 1\n",
    )
    assert code == 0
    assert "SELECT（またはWITH）以外は実行できません" in out


def test_play_reports_sql_error() -> None:
    code, out = run_cli(
        ["play", "--seed", "3"], "SELECT * FROM nonexistent\n"
    )
    assert code == 0
    assert "SQLエラー" in out


def test_play_help_command() -> None:
    code, out = run_cli(["play", "--seed", "3"], "help\n")
    assert code == 0
    assert "【利用できるテーブル】" in out


def test_play_hint_is_opt_in() -> None:
    """ヒントは最初から出さず、'hint' で取りに行くこと（推理を奪わない）。"""
    code, out = run_cli(["play", "--seed", "3"], "")
    assert code == 0
    assert "ヒント:" not in out

    code, out = run_cli(["play", "--seed", "3"], "hint\n")
    assert code == 0
    assert "ヒント:" in out


def test_hint_does_not_reveal_the_answer_query() -> None:
    """ヒントに述語SQLそのもの（正解クエリ）を含めないこと。"""
    from sql_detective.case import TEMPLATES

    for template in TEMPLATES:
        hint = template.hint
        assert "SELECT" not in hint.upper()
        assert "GROUP BY" not in hint.upper()
        assert "JOIN" not in hint.upper()
        assert "HAVING" not in hint.upper()


def test_play_empty_line_exits() -> None:
    code, out = run_cli(["play", "--seed", "3"], "")
    assert code == 0


def test_play_shows_remaining_budget() -> None:
    code, out = run_cli(
        ["play", "--seed", "3"], "SELECT * FROM users LIMIT 1\n"
    )
    assert code == 0
    assert "残り捜査コスト: 7/8" in out


def test_play_truncation_notice() -> None:
    code, out = run_cli(
        ["play", "--seed", "3"],
        "SELECT u.id, l.id FROM users u CROSS JOIN logins l\n",
    )
    assert code == 0
    assert "先頭100行のみ表示" in out


def test_play_budget_exhaustion_loses() -> None:
    """コストを使い切った状態でさらに捜査しようとすると敗北すること。"""
    queries = "\n".join(["SELECT * FROM users"] * 5)
    code, out = run_cli(["play", "--seed", "3"], queries)
    assert code == 0
    assert "【敗北】" in out
    assert "捜査コストを使い切った" in out


def test_play_unknown_seed_still_works() -> None:
    code, out = run_cli(["play", "--seed", "99999"], "")
    assert code == 0
    assert "【依頼】" in out


def test_play_without_seed_works() -> None:
    code, out = run_cli(["play"], "")
    assert code == 0
    assert "【依頼】" in out
