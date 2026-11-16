"""Issue #3: コアループ（捜査→告発→判定）のテスト。"""

from __future__ import annotations

from pathlib import Path

import pytest

from sql_detective.db import QueryRejected
from sql_detective.game import MAX_QUERIES, Game, LossReason, Phase


@pytest.fixture()
def game(tmp_path: Path):
    with Game.new(seed=3, workdir=tmp_path) as g:
        yield g


# --- 初期状態 ---------------------------------------------------------------


def test_starts_in_briefing(game: Game) -> None:
    assert game.phase is Phase.BRIEFING
    assert game.queries_used == 0
    assert game.budget_left == MAX_QUERIES
    assert not game.is_over


def test_brief_is_available_before_start(game: Game) -> None:
    assert game.case.template.brief
    assert game.case.template.title


def test_start_moves_to_investigating(game: Game) -> None:
    game.start()
    assert game.phase is Phase.INVESTIGATING


# --- 捜査コスト -------------------------------------------------------------


def test_query_decrements_budget(game: Game) -> None:
    game.investigate("SELECT * FROM users LIMIT 3")
    assert game.queries_used == 1
    assert game.budget_left == MAX_QUERIES - 1


def test_unlimited_query_costs_two(game: Game) -> None:
    game.investigate("SELECT * FROM users")
    assert game.queries_used == 2
    assert game.budget_left == MAX_QUERIES - 2


def test_budget_never_goes_negative(game: Game) -> None:
    for _ in range(MAX_QUERIES):
        if not game.can_investigate:
            break
        game.investigate("SELECT * FROM users LIMIT 1")
    assert game.budget_left >= 0


def test_cannot_investigate_after_budget_exhausted(game: Game) -> None:
    for _ in range(MAX_QUERIES):
        if not game.can_investigate:
            break
        game.investigate("SELECT * FROM users LIMIT 1")
    assert game.budget_left == 0
    assert not game.can_investigate
    with pytest.raises(RuntimeError):
        game.investigate("SELECT * FROM users LIMIT 1")
    assert game.phase is Phase.LOST
    assert game.loss_reason is LossReason.OUT_OF_BUDGET


def test_rejected_query_does_not_consume_budget(game: Game) -> None:
    with pytest.raises(QueryRejected):
        game.investigate("DELETE FROM users")
    assert game.queries_used == 0


def test_history_records_results(game: Game) -> None:
    game.investigate("SELECT * FROM users LIMIT 2")
    game.investigate("SELECT COUNT(*) FROM orders")
    assert len(game.history) == 2
    assert game.history[0].row_count == 2


# --- 告発と判定 -------------------------------------------------------------


def test_correct_accusation_wins(game: Game) -> None:
    game.investigate("SELECT * FROM users LIMIT 1")
    result = game.accuse(str(game.case.culprit_id))
    assert result.correct is True
    assert game.phase is Phase.WON
    assert result.score == game.budget_left


def test_correct_accusation_by_name(game: Game) -> None:
    result = game.accuse(game.case.culprit_name)
    assert result.correct is True
    assert game.phase is Phase.WON


def test_wrong_accusation_loses_immediately(game: Game) -> None:
    other = next(
        int(row[0])
        for row in game.case.users
        if int(row[0]) != game.case.culprit_id
    )
    result = game.accuse(str(other))
    assert result.correct is False
    assert game.phase is Phase.LOST
    assert game.loss_reason is LossReason.WRONG_ACCUSATION
    assert result.reason is LossReason.WRONG_ACCUSATION


def test_wrong_accusation_ends_game(game: Game) -> None:
    other = next(
        int(row[0])
        for row in game.case.users
        if int(row[0]) != game.case.culprit_id
    )
    game.accuse(str(other))
    with pytest.raises(RuntimeError):
        game.investigate("SELECT * FROM users LIMIT 1")
    with pytest.raises(RuntimeError):
        game.accuse(str(game.case.culprit_id))


def test_accusation_is_free(game: Game) -> None:
    """告発は捜査コストを消費しないこと。"""
    before = game.budget_left
    game.accuse(str(game.case.culprit_id))
    assert game.budget_left == before


# --- スコア -----------------------------------------------------------------


def test_score_equals_remaining_budget_on_win(game: Game) -> None:
    game.investigate("SELECT * FROM users LIMIT 1")
    game.investigate("SELECT * FROM users LIMIT 1")
    game.accuse(str(game.case.culprit_id))
    assert game.score == MAX_QUERIES - 2


def test_perfect_score_when_no_queries_used(game: Game) -> None:
    game.accuse(str(game.case.culprit_id))
    assert game.score == MAX_QUERIES


def test_score_is_zero_on_loss(game: Game) -> None:
    other = next(
        int(row[0])
        for row in game.case.users
        if int(row[0]) != game.case.culprit_id
    )
    game.accuse(str(other))
    assert game.score == 0


# --- 通しプレイ -------------------------------------------------------------


def test_full_playthrough_with_real_investigation(tmp_path: Path) -> None:
    """依頼→捜査→告発まで、実際に検証クエリで犯人を特定できること。"""
    with Game.new(seed=21, workdir=tmp_path) as g:
        g.start()
        result = g.investigate(g.case.template.predicate_sql)
        assert result.row_count == 1
        solved_id = int(result.rows[0][0])
        assert solved_id == g.case.culprit_id

        accusation = g.accuse(str(solved_id))
        assert accusation.correct is True
        assert g.phase is Phase.WON
        assert g.score == MAX_QUERIES - result.cost


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5, 10, 25, 50, 75, 100])
def test_every_case_is_solvable_by_its_predicate(seed: int, tmp_path: Path) -> None:
    """全事件が検証クエリ1本で解けること（解けない事件を出さない）。"""
    with Game.new(seed=seed, workdir=tmp_path) as g:
        result = g.investigate(g.case.template.predicate_sql)
        assert result.row_count == 1
        assert int(result.rows[0][0]) == g.case.culprit_id
