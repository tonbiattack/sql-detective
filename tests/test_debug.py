"""デバッグモードと通常プレイの分離を検証する。"""

from __future__ import annotations

import io
from contextlib import redirect_stdout

import pytest

from sql_detective.case import generate_case
from sql_detective.cli import main


def run_cli(monkeypatch: pytest.MonkeyPatch, argv: list[str], lines: list[str]) -> str:
    inputs = iter(lines)

    def read_input(prompt: str) -> str:
        print(prompt, end="")
        try:
            return next(inputs)
        except StopIteration:
            raise EOFError from None

    monkeypatch.setattr("builtins.input", read_input)
    output = io.StringIO()
    with redirect_stdout(output):
        assert main(argv) == 0
    return output.getvalue()


def test_debug_status_answer_verify_and_history(monkeypatch: pytest.MonkeyPatch) -> None:
    case = generate_case(3)
    output = run_cli(monkeypatch, ["debug", "--seed", "3"], [
        "SELECT * FROM users LIMIT 1", ":status", ":answer", ":verify"
    ])
    assert "【デバッグモード】" in output
    assert "シード: 3" in output
    assert case.template.key in output
    assert "捜査履歴: 1件" in output
    assert "SELECT * FROM users LIMIT 1" in output
    assert f"犯人: {case.culprit_name}（ID {case.culprit_id}）" in output
    assert "一意性検証SQL:" in output
    assert f"候補ID: [{case.culprit_id}]" in output
    assert "残り捜査コスト: 7/8" in output


def test_debug_budget_validation_and_reset(monkeypatch: pytest.MonkeyPatch) -> None:
    output = run_cli(monkeypatch, ["debug", "--seed", "3"], [
        ":budget", ":budget x", ":budget -1", ":budget 9", ":budget 2",
        "SELECT 1", ":status", ":reset", ":status"
    ])
    assert "使い方: :budget" in output
    assert "整数を指定" in output
    assert output.count("0〜8で指定") == 2
    assert "残り捜査コストを 2 に変更" in output
    assert "残り 1/8 / 使用 7" in output
    assert "捜査履歴: 1件" in output
    assert "同じ事件を初期状態に戻しました" in output
    assert "残り 8/8 / 使用 0" in output
    assert "捜査履歴: 0件" in output


def test_debug_reset_after_loss_and_replay(monkeypatch: pytest.MonkeyPatch) -> None:
    case = generate_case(3)
    wrong = next(int(row[0]) for row in case.users if row[0] != case.culprit_id)
    output = run_cli(monkeypatch, ["debug", "--seed", "3"], [
        "accuse", str(wrong), ":status", ":budget 5", ":reset",
        "accuse", str(case.culprit_id), ":status"
    ])
    assert "【敗北】" in output
    assert "状態: lost / 敗北理由: wrong_accusation" in output
    assert "終了後は :reset" in output
    assert "【解決】" in output
    assert "状態: won / 敗北理由: なし" in output
    assert "スコア: 8" in output


def test_debug_exhaustion_can_be_reset(monkeypatch: pytest.MonkeyPatch) -> None:
    output = run_cli(monkeypatch, ["debug", "--seed", "3"], [
        ":budget 0", "SELECT 1", ":status", ":reset", "SELECT 1", ":status"
    ])
    assert "状態: lost / 敗北理由: out_of_budget" in output
    assert "状態: investigating / 敗北理由: なし" in output
    assert "残り 7/8 / 使用 1" in output


def test_normal_play_rejects_debug_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    output = run_cli(monkeypatch, ["play", "--seed", "3"], [":answer", ":budget 8"])
    assert "【デバッグモード】" not in output
    assert "一意性検証SQL:" not in output
    assert "残り捜査コストを 8 に変更" not in output
    assert "残り捜査コスト: 8/8" in output


def test_debug_retains_read_only_query_restrictions(monkeypatch: pytest.MonkeyPatch) -> None:
    output = run_cli(monkeypatch, ["debug", "--seed", "3"], [
        ":help", "DELETE FROM users", "SELECT COUNT(*) FROM users", ":status"
    ])
    assert "【デバッグコマンド】" in output
    assert "SELECT（またはWITH）以外は実行できません" in output
    assert "捜査履歴: 1件" in output


def test_debug_displays_generated_seed(monkeypatch: pytest.MonkeyPatch) -> None:
    output = run_cli(monkeypatch, ["debug"], [":status"])
    assert output.count("シード:") >= 2
