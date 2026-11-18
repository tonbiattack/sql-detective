"""Web版の事件データ生成と、同梱データの整合性を検証する。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from sql_detective.case import generate_case, verify_case_db
from sql_detective.web_cases import BRIEFS, EVIDENCE, build_cases, export_case

WEB_DIR = Path(__file__).resolve().parents[1] / "web"
CASES_JSON = WEB_DIR / "cases.json"


@pytest.fixture(scope="module")
def dataset() -> dict:
    return json.loads(CASES_JSON.read_text(encoding="utf-8"))


# --- 同梱データ -------------------------------------------------------------


def test_dataset_has_100_cases(dataset: dict) -> None:
    assert dataset["version"] == 1
    assert len(dataset["cases"]) == 100


def test_dataset_has_no_answer_field(dataset: dict) -> None:
    """答えをJSONに直接持たせない（証拠から導く）。"""
    for case in dataset["cases"]:
        assert "answerId" not in case
        assert "culprit" not in json.dumps(case, ensure_ascii=False).lower()


def test_every_case_has_evidence_and_suspects(dataset: dict) -> None:
    for case in dataset["cases"]:
        assert len(case["suspects"]) == 10
        assert len(case["evidence"]) >= 2
        assert case["brief"]
        assert case["title"]


def test_decisive_evidence_is_unique(dataset: dict) -> None:
    """決め手の証拠がちょうど1人を指すこと（一意解）。"""
    for case in dataset["cases"]:
        assert len(case["evidence"][-1]["candidateIds"]) == 1, case["seed"]


def test_candidates_narrow_monotonically(dataset: dict) -> None:
    """証拠を開くほど候補が増えないこと。"""
    for case in dataset["cases"]:
        previous = {suspect["id"] for suspect in case["suspects"]}
        for card in case["evidence"]:
            current = set(card["candidateIds"])
            assert current <= previous, case["seed"]
            assert current, case["seed"]
            previous = current


def test_culprit_is_always_a_candidate(dataset: dict) -> None:
    for case in dataset["cases"]:
        culprit = case["evidence"][-1]["candidateIds"][0]
        assert culprit in {suspect["id"] for suspect in case["suspects"]}


def test_evidence_rows_match_columns(dataset: dict) -> None:
    for case in dataset["cases"]:
        for card in case["evidence"]:
            assert card["columns"]
            for row in card["rows"]:
                assert len(row) == len(card["columns"])


def test_evidence_candidate_ids_come_from_rows(dataset: dict) -> None:
    """候補は表示するSQLの結果から作られていること。"""
    for case in dataset["cases"]:
        for card in case["evidence"]:
            from_rows = {int(row[0]) for row in card["rows"]}
            assert set(card["candidateIds"]) == from_rows, case["seed"]


def test_all_case_types_are_present(dataset: dict) -> None:
    assert {case["type"] for case in dataset["cases"]} == set(EVIDENCE)


def test_briefs_avoid_ambiguous_wording(dataset: dict) -> None:
    """「昨夜」「連続」など、検証SQLと食い違う表現を使わない。"""
    for case in dataset["cases"]:
        assert "昨夜" not in case["brief"]
        assert "連続" not in case["brief"]


# --- 生成ロジック -----------------------------------------------------------


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_export_case_matches_cli_verification(seed: int, tmp_path: Path) -> None:
    """Web用データの正解が、CLIの一意性検証と一致すること。"""
    case = generate_case(seed)
    path = case.write_db(tmp_path / "case.db")
    data = export_case(case, path)
    assert data["evidence"][-1]["candidateIds"] == verify_case_db(path, case.template)
    assert data["evidence"][-1]["candidateIds"] == [case.culprit_id]


def test_export_rejects_inconsistent_case(tmp_path: Path) -> None:
    """証拠から一意に決まらない事件は配布しない。"""
    case = generate_case(3)
    path = case.write_db(tmp_path / "case.db")
    with sqlite3.connect(path) as conn:
        conn.execute("DELETE FROM logins WHERE success = 0")
        conn.commit()
    with pytest.raises(ValueError):
        export_case(case, path)


def test_build_cases_count() -> None:
    data = build_cases(3)
    assert len(data["cases"]) == 3
    assert [case["seed"] for case in data["cases"]] == [1, 2, 3]


def test_build_cases_rejects_zero() -> None:
    with pytest.raises(ValueError):
        build_cases(0)


def test_every_template_has_evidence_and_brief() -> None:
    assert set(EVIDENCE) == set(BRIEFS)
    for cards in EVIDENCE.values():
        assert len(cards) >= 2
        for card in cards:
            assert card.title and card.description
            assert card.sql.strip().upper().startswith("SELECT")
