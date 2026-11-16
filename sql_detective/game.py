"""コアループ実装（Issue #3）。

依頼提示 → 捜査（クエリ実行）→ 告発 → 判定 の流れを状態機械として持つ。
UI（CLI）はこのクラスを操作するだけで、判定ロジックはここに閉じる。
"""

from __future__ import annotations

import random
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from .case import Case, generate_case
from .db import QueryRejected, QueryResult, QueryTimeout, ReadOnlyDatabase

# --- 仕様値（docs/SPEC.md と対応） -----------------------------------------

MAX_QUERIES = 8


class Phase(str, Enum):
    """ゲームの進行状態。"""

    BRIEFING = "briefing"
    INVESTIGATING = "investigating"
    WON = "won"
    LOST = "lost"


class LossReason(str, Enum):
    """敗北の理由。"""

    WRONG_ACCUSATION = "wrong_accusation"
    OUT_OF_BUDGET = "out_of_budget"


@dataclass(frozen=True)
class AccusationResult:
    """告発の判定結果。"""

    correct: bool
    accused: str
    culprit_name: str
    score: int
    reason: LossReason | None = None


@dataclass
class Game:
    """1事件ぶんのプレイ状態。"""

    case: Case
    db_path: Path
    max_queries: int = MAX_QUERIES
    queries_used: int = 0
    phase: Phase = Phase.BRIEFING
    history: list[QueryResult] = field(default_factory=list)
    loss_reason: LossReason | None = None
    _db: ReadOnlyDatabase | None = field(default=None, repr=False)

    # -- 生成 ---------------------------------------------------------------

    @classmethod
    def new(
        cls,
        seed: int | None = None,
        *,
        workdir: str | Path | None = None,
        max_queries: int = MAX_QUERIES,
    ) -> "Game":
        """事件を生成してゲームを開始する。"""
        if seed is None:
            seed = random.SystemRandom().randrange(1, 2**31)
        case = generate_case(seed)

        if workdir is None:
            workdir = tempfile.mkdtemp(prefix="sql-detective-")
        db_path = Path(workdir) / f"case-{case.seed}.db"
        case.write_db(db_path)
        return cls(case=case, db_path=db_path, max_queries=max_queries)

    # -- 状態 ---------------------------------------------------------------

    @property
    def budget_left(self) -> int:
        return max(0, self.max_queries - self.queries_used)

    @property
    def is_over(self) -> bool:
        return self.phase in (Phase.WON, Phase.LOST)

    @property
    def can_investigate(self) -> bool:
        """まだ捜査できるか（コストが残っているか）。"""
        return not self.is_over and self.budget_left > 0

    @property
    def score(self) -> int:
        """スコア＝残りクエリ数（勝利時のみ意味を持つ）。"""
        return self.budget_left if self.phase is Phase.WON else 0

    # -- 進行 ---------------------------------------------------------------

    def start(self) -> None:
        """依頼を提示し、捜査フェーズへ移る。"""
        if self.phase is Phase.BRIEFING:
            self.phase = Phase.INVESTIGATING

    def _ensure_db(self) -> ReadOnlyDatabase:
        if self._db is None:
            self._db = ReadOnlyDatabase(self.db_path)
        return self._db

    def close(self) -> None:
        if self._db is not None:
            self._db.close()
            self._db = None

    def __enter__(self) -> "Game":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- 捜査 ---------------------------------------------------------------

    def investigate(self, sql: str) -> QueryResult:
        """クエリを1回実行する。コストは結果に応じて1または2。

        コストが0になった時点で捜査は打ち切り。告発は無料でいつでも可能なので、
        最後の1回を使っても即敗北にはならない（スコアが0になるだけ）。
        コスト0でさらに捜査しようとした場合に「使い切った」敗北となる。
        """
        if self.phase is Phase.BRIEFING:
            self.start()
        if self.is_over:
            raise RuntimeError("事件はすでに終了しています。")
        if self.budget_left <= 0:
            self.phase = Phase.LOST
            self.loss_reason = LossReason.OUT_OF_BUDGET
            raise RuntimeError(
                "捜査コストを使い切りました。告発してください。"
            )

        result = self._ensure_db().execute(sql)
        self.queries_used += result.cost
        self.history.append(result)
        return result

    # -- 告発 ---------------------------------------------------------------

    def accuse(self, answer: str) -> AccusationResult:
        """容疑者を1人告発し、判定する。外せば即終了。"""
        if self.phase is Phase.BRIEFING:
            self.start()
        if self.is_over:
            raise RuntimeError("事件はすでに終了しています。")

        correct = self.case.accepts_answer(answer)
        if correct:
            self.phase = Phase.WON
            self.loss_reason = None
            return AccusationResult(
                correct=True,
                accused=answer.strip(),
                culprit_name=self.case.culprit_name,
                score=self.score,
            )

        self.phase = Phase.LOST
        self.loss_reason = LossReason.WRONG_ACCUSATION
        return AccusationResult(
            correct=False,
            accused=answer.strip(),
            culprit_name=self.case.culprit_name,
            score=0,
            reason=LossReason.WRONG_ACCUSATION,
        )


__all__ = [
    "MAX_QUERIES",
    "AccusationResult",
    "Game",
    "LossReason",
    "Phase",
    "QueryRejected",
    "QueryTimeout",
]
