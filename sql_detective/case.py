"""事件生成（Issue #4）。

固定4テーブル（users / orders / logins / transfers）へ、解が一意になる事件データを
**逆算生成**する。生成後は必ず検証クエリで「条件を満たすのが1人だけ」を確認し、
満たさなければ別シードで再生成する。複数解の事件は絶対に出さない。
"""

from __future__ import annotations

import random
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Mapping

from .db import SCHEMA_SQL, TABLE_COLUMNS, ReadOnlyDatabase

# --- 定数 -------------------------------------------------------------------

BASE_DATE = date(2026, 11, 15)
USER_COUNT = 10
ADMIN_COUNT = 2

DEPARTMENTS = ("営業部", "経理部", "開発部", "総務部", "人事部")

NAME_POOL = (
    "佐藤 健一",
    "鈴木 美咲",
    "高橋 亮",
    "田中 由紀",
    "伊藤 大輔",
    "渡辺 彩",
    "山本 翔太",
    "中村 千夏",
    "小林 直樹",
    "加藤 恵",
    "吉田 拓也",
    "山田 真由",
    "佐々木 涼",
    "松本 沙織",
    "井上 隼人",
    "木村 麻衣",
    "林 悠斗",
    "清水 玲奈",
    "山口 誠",
    "森 あかり",
)

MEMOS = ("11月分精算", "立替返金", "備品購入", "交通費", "外注費", "会費", "書籍代")

STATUSES = ("paid", "paid", "paid", "pending", "cancelled")


class CaseGenerationError(RuntimeError):
    """一意解の事件を生成できなかった。"""


# --- 事件テンプレート -------------------------------------------------------


@dataclass(frozen=True)
class CaseTemplate:
    """事件の種類。``predicate_sql`` は条件を満たす user_id を返す検証クエリ。"""

    key: str
    title: str
    brief: str
    hint: str
    predicate_sql: str


TEMPLATES: tuple[CaseTemplate, ...] = (
    CaseTemplate(
        key="midnight_transfer",
        title="深夜の送金",
        brief=(
            "昨夜0時から5時のあいだに、50万円以上の送金が記録されている。"
            "その送金を実行した社員は1人だけのはずだ。誰か。"
        ),
        hint="送金ログには時刻と金額が入っている。両方を同時に見る必要がある。",
        predicate_sql="""
            SELECT DISTINCT from_user_id AS user_id
            FROM transfers
            WHERE amount >= 500000
              AND time(created_at) >= '00:00:00'
              AND time(created_at) <  '05:00:00'
        """,
    ),
    CaseTemplate(
        key="login_bruteforce",
        title="同一IPからの連続失敗",
        brief=(
            "同じIPアドレスから3回以上ログインに失敗している社員が1人いる。"
            "その社員は誰か。"
        ),
        hint="ログインログには接続元IPが残っている。誰がどこから来たかを見る。",
        predicate_sql="""
            SELECT DISTINCT user_id
            FROM logins
            WHERE success = 0
            GROUP BY user_id, ip
            HAVING COUNT(*) >= 3
        """,
    ),
    CaseTemplate(
        key="bulk_orders",
        title="高額注文の連続",
        brief=(
            "同じ日に10万円以上の注文を3件以上出した社員が1人だけいる。"
            "その社員は誰か。"
        ),
        hint="注文ログの日付は created_at から取れる。同じ日かどうかを意識する。",
        predicate_sql="""
            SELECT DISTINCT user_id
            FROM orders
            WHERE amount >= 100000
            GROUP BY user_id, date(created_at)
            HAVING COUNT(*) >= 3
        """,
    ),
    CaseTemplate(
        key="admin_impersonation",
        title="管理者への不自然な送金",
        brief=(
            "管理者ではない社員が、管理者へ30万円以上を送金している。"
            "該当する送金者は1人だけのはずだ。誰か。"
        ),
        hint="送り手と受け手は別の人物。users を2回参照する必要がある。",
        predicate_sql="""
            SELECT DISTINCT t.from_user_id AS user_id
            FROM transfers AS t
            JOIN users AS sender   ON sender.id   = t.from_user_id
            JOIN users AS receiver ON receiver.id = t.to_user_id
            WHERE sender.is_admin = 0
              AND receiver.is_admin = 1
              AND t.amount >= 300000
        """,
    ),
)

TEMPLATES_BY_KEY: Mapping[str, CaseTemplate] = {t.key: t for t in TEMPLATES}


# --- 事件データ -------------------------------------------------------------


@dataclass(frozen=True)
class Case:
    """1事件ぶんのデータ一式。"""

    seed: int
    attempt: int
    template: CaseTemplate
    culprit_id: int
    culprit_name: str
    tables: Mapping[str, tuple[tuple[object, ...], ...]]

    # -- 参照用 -------------------------------------------------------------

    @property
    def users(self) -> tuple[tuple[object, ...], ...]:
        return self.tables["users"]

    def user_name(self, user_id: int) -> str:
        for row in self.users:
            if row[0] == user_id:
                return str(row[1])
        raise KeyError(user_id)

    def accepts_answer(self, text: str) -> bool:
        """告発の入力（ID または氏名）が犯人を指しているか。"""
        answer = text.strip()
        if not answer:
            return False
        if answer.isdigit() and int(answer) == self.culprit_id:
            return True
        normalized = answer.replace(" ", "").replace("　", "")
        culprit = self.culprit_name.replace(" ", "").replace("　", "")
        return normalized == culprit

    # -- 永続化 -------------------------------------------------------------

    def write_db(self, path: str | Path) -> Path:
        """事件DBをSQLiteファイルとして書き出す。"""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            target.unlink()
        conn = sqlite3.connect(target)
        try:
            conn.executescript(SCHEMA_SQL)
            for table in TABLE_COLUMNS:
                rows = self.tables[table]
                if not rows:
                    continue
                placeholders = ", ".join("?" for _ in TABLE_COLUMNS[table])
                conn.executemany(
                    f"INSERT INTO {table} VALUES ({placeholders})", rows
                )
            conn.commit()
        finally:
            conn.close()
        return target


# --- 検証 -------------------------------------------------------------------


def find_matching_users(db: ReadOnlyDatabase, template: CaseTemplate) -> list[int]:
    """検証クエリを実行し、条件を満たす user_id を昇順で返す。"""
    result = db.execute(template.predicate_sql)
    return sorted({int(row[0]) for row in result.rows})


def verify_case_db(db_path: str | Path, template: CaseTemplate) -> list[int]:
    """生成済みDBファイルに対して一意性検証クエリを実行する。"""
    with ReadOnlyDatabase(db_path) as db:
        return find_matching_users(db, template)


# --- 生成 -------------------------------------------------------------------


def _ts(day_offset: int, hour: int, minute: int, second: int = 0) -> str:
    moment = datetime.combine(BASE_DATE, datetime.min.time()) + timedelta(
        days=day_offset, hours=hour, minutes=minute, seconds=second
    )
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def _build_users(rng: random.Random) -> list[tuple[object, ...]]:
    names = list(NAME_POOL)
    rng.shuffle(names)
    admin_ids = set(rng.sample(range(1, USER_COUNT + 1), ADMIN_COUNT))
    users: list[tuple[object, ...]] = []
    for user_id in range(1, USER_COUNT + 1):
        joined = BASE_DATE - timedelta(days=rng.randint(200, 2400))
        users.append(
            (
                user_id,
                names[user_id - 1],
                rng.choice(DEPARTMENTS),
                joined.isoformat(),
                1 if user_id in admin_ids else 0,
            )
        )
    return users


def _other_user(rng: random.Random, user_id: int, candidates: list[int]) -> int:
    pool = [uid for uid in candidates if uid != user_id]
    return rng.choice(pool)


def _gen_midnight_transfer(
    rng: random.Random, users: list[tuple[object, ...]], culprit_id: int
) -> tuple[list, list, list]:
    """深夜の高額送金。犯人のみが「深夜かつ高額」を満たす。"""
    all_ids = [int(row[0]) for row in users]
    transfers: list[tuple[object, ...]] = []

    # 犯人の決め手: 深夜0〜5時・50万円以上（1件だけ）
    transfers.append(
        (
            culprit_id,
            _other_user(rng, culprit_id, all_ids),
            rng.randint(500000, 900000),
            _ts(0, rng.randint(0, 4), rng.randint(0, 59), rng.randint(0, 59)),
            rng.choice(MEMOS),
        )
    )

    # ノイズ1: 日中（9〜21時）の送金。高額でも時刻で外れる
    for _ in range(rng.randint(18, 26)):
        sender = rng.choice(all_ids)
        amount = rng.randint(500000, 900000) if rng.random() < 0.35 else rng.randint(
            3000, 400000
        )
        transfers.append(
            (
                sender,
                _other_user(rng, sender, all_ids),
                amount,
                _ts(rng.randint(-3, 0), rng.randint(9, 21), rng.randint(0, 59)),
                rng.choice(MEMOS),
            )
        )

    # ノイズ2: 深夜の少額送金。時刻は合うが金額で外れる
    for _ in range(rng.randint(4, 7)):
        sender = rng.choice(all_ids)
        transfers.append(
            (
                sender,
                _other_user(rng, sender, all_ids),
                rng.randint(3000, 400000),
                _ts(rng.randint(-3, 0), rng.randint(0, 4), rng.randint(0, 59)),
                rng.choice(MEMOS),
            )
        )

    orders = _gen_orders(rng, all_ids)
    logins = _gen_logins(rng, all_ids)
    return orders, logins, transfers


def _gen_login_bruteforce(
    rng: random.Random, users: list[tuple[object, ...]], culprit_id: int
) -> tuple[list, list, list]:
    """同一IPからの連続失敗。犯人のみが同一IPで3回以上失敗する。"""
    all_ids = [int(row[0]) for row in users]
    logins: list[tuple[object, ...]] = []

    def ip() -> str:
        return f"10.{rng.randint(0, 255)}.{rng.randint(0, 255)}.{rng.randint(1, 254)}"

    bad_ip = ip()
    # 犯人の決め手: 同一IPから4回失敗
    for _ in range(4):
        logins.append(
            (
                culprit_id,
                _ts(rng.randint(-2, 0), rng.randint(0, 23), rng.randint(0, 59)),
                bad_ip,
                0,
            )
        )
    # 犯人の正常ログイン（別IP）
    for _ in range(rng.randint(2, 4)):
        logins.append(
            (
                culprit_id,
                _ts(rng.randint(-3, 0), rng.randint(8, 20), rng.randint(0, 59)),
                ip(),
                1,
            )
        )

    # ノイズ: 各ユーザーは同一IPで最大2回失敗まで
    for user_id in all_ids:
        if user_id == culprit_id:
            continue
        for _ in range(rng.randint(0, 2)):
            noise_ip = ip()
            for _ in range(rng.randint(1, 2)):
                logins.append(
                    (
                        user_id,
                        _ts(rng.randint(-3, 0), rng.randint(0, 23), rng.randint(0, 59)),
                        noise_ip,
                        0,
                    )
                )
        for _ in range(rng.randint(1, 4)):
            logins.append(
                (
                    user_id,
                    _ts(rng.randint(-3, 0), rng.randint(7, 22), rng.randint(0, 59)),
                    ip(),
                    1,
                )
            )

    # ニアミス: 3回失敗だが毎回IPが違うユーザーを1人入れる
    decoy = _other_user(rng, culprit_id, all_ids)
    for _ in range(3):
        logins.append(
            (
                decoy,
                _ts(rng.randint(-3, 0), rng.randint(0, 23), rng.randint(0, 59)),
                ip(),
                0,
            )
        )

    orders = _gen_orders(rng, all_ids)
    transfers = _gen_transfers(rng, all_ids)
    return orders, logins, transfers


def _gen_bulk_orders(
    rng: random.Random, users: list[tuple[object, ...]], culprit_id: int
) -> tuple[list, list, list]:
    """高額注文の連続。犯人のみが同一日に3件以上出す。"""
    all_ids = [int(row[0]) for row in users]
    orders: list[tuple[object, ...]] = []

    # 犯人の決め手: 同じ日に10万円以上を3件
    for _ in range(3):
        orders.append(
            (
                culprit_id,
                rng.randint(100000, 300000),
                _ts(0, rng.randint(9, 20), rng.randint(0, 59)),
                rng.choice(STATUSES),
            )
        )

    # ノイズ: 同一日の高額注文は最大2件まで
    for user_id in all_ids:
        if user_id == culprit_id:
            continue
        for day in range(-3, 1):
            for _ in range(rng.randint(0, 2)):
                orders.append(
                    (
                        user_id,
                        rng.randint(100000, 300000),
                        _ts(day, rng.randint(9, 20), rng.randint(0, 59)),
                        rng.choice(STATUSES),
                    )
                )
        for _ in range(rng.randint(1, 4)):
            orders.append(
                (
                    user_id,
                    rng.randint(1000, 99999),
                    _ts(rng.randint(-3, 0), rng.randint(9, 20), rng.randint(0, 59)),
                    rng.choice(STATUSES),
                )
            )

    # ニアミス: 高額3件だが日付がばらけたユーザーを1人入れる
    decoy = _other_user(rng, culprit_id, all_ids)
    for day in (-3, -2, -1):
        orders.append(
            (
                decoy,
                rng.randint(100000, 300000),
                _ts(day, rng.randint(9, 20), rng.randint(0, 59)),
                rng.choice(STATUSES),
            )
        )

    logins = _gen_logins(rng, all_ids)
    transfers = _gen_transfers(rng, all_ids)
    return orders, logins, transfers


def _gen_admin_impersonation(
    rng: random.Random, users: list[tuple[object, ...]], culprit_id: int
) -> tuple[list, list, list]:
    """管理者への不自然な送金。非管理者→管理者の高額送金は犯人だけ。"""
    all_ids = [int(row[0]) for row in users]
    admin_ids = [int(row[0]) for row in users if int(row[4]) == 1]
    non_admin_ids = [uid for uid in all_ids if uid not in admin_ids]
    transfers: list[tuple[object, ...]] = []

    # 犯人の決め手: 非管理者 → 管理者 へ30万円以上
    transfers.append(
        (
            culprit_id,
            rng.choice(admin_ids),
            rng.randint(300000, 800000),
            _ts(rng.randint(-2, 0), rng.randint(9, 21), rng.randint(0, 59)),
            rng.choice(MEMOS),
        )
    )

    # ノイズ1: 管理者 → 管理者 の高額送金（送り手が管理者なので外れる）
    for _ in range(rng.randint(3, 6)):
        sender = rng.choice(admin_ids)
        transfers.append(
            (
                sender,
                _other_user(rng, sender, admin_ids),
                rng.randint(300000, 800000),
                _ts(rng.randint(-3, 0), rng.randint(9, 21), rng.randint(0, 59)),
                rng.choice(MEMOS),
            )
        )

    # ノイズ2: 非管理者 → 管理者 の少額送金（金額で外れる）
    for _ in range(rng.randint(6, 10)):
        sender = rng.choice(non_admin_ids)
        transfers.append(
            (
                sender,
                rng.choice(admin_ids),
                rng.randint(3000, 299999),
                _ts(rng.randint(-3, 0), rng.randint(9, 21), rng.randint(0, 59)),
                rng.choice(MEMOS),
            )
        )

    # ノイズ3: 非管理者 → 非管理者 の高額送金（受け手が管理者でないので外れる）
    for _ in range(rng.randint(6, 10)):
        sender = rng.choice(non_admin_ids)
        transfers.append(
            (
                sender,
                _other_user(rng, sender, non_admin_ids),
                rng.randint(300000, 800000),
                _ts(rng.randint(-3, 0), rng.randint(9, 21), rng.randint(0, 59)),
                rng.choice(MEMOS),
            )
        )

    orders = _gen_orders(rng, all_ids)
    logins = _gen_logins(rng, all_ids)
    return orders, logins, transfers


def _gen_orders(rng: random.Random, all_ids: list[int]) -> list[tuple[object, ...]]:
    orders: list[tuple[object, ...]] = []
    for _ in range(rng.randint(40, 60)):
        orders.append(
            (
                rng.choice(all_ids),
                rng.randint(1000, 300000),
                _ts(rng.randint(-3, 0), rng.randint(9, 21), rng.randint(0, 59)),
                rng.choice(STATUSES),
            )
        )
    return orders


def _gen_logins(rng: random.Random, all_ids: list[int]) -> list[tuple[object, ...]]:
    logins: list[tuple[object, ...]] = []
    for _ in range(rng.randint(50, 70)):
        logins.append(
            (
                rng.choice(all_ids),
                _ts(rng.randint(-3, 0), rng.randint(7, 22), rng.randint(0, 59)),
                f"10.{rng.randint(0, 255)}.{rng.randint(0, 255)}.{rng.randint(1, 254)}",
                1 if rng.random() < 0.85 else 0,
            )
        )
    return logins


def _gen_transfers(rng: random.Random, all_ids: list[int]) -> list[tuple[object, ...]]:
    transfers: list[tuple[object, ...]] = []
    for _ in range(rng.randint(20, 30)):
        sender = rng.choice(all_ids)
        transfers.append(
            (
                sender,
                _other_user(rng, sender, all_ids),
                rng.randint(3000, 400000),
                _ts(rng.randint(-3, 0), rng.randint(9, 21), rng.randint(0, 59)),
                rng.choice(MEMOS),
            )
        )
    return transfers


_GENERATORS = {
    "midnight_transfer": _gen_midnight_transfer,
    "login_bruteforce": _gen_login_bruteforce,
    "bulk_orders": _gen_bulk_orders,
    "admin_impersonation": _gen_admin_impersonation,
}


def _assign_ids(
    orders: list[tuple[object, ...]],
    logins: list[tuple[object, ...]],
    transfers: list[tuple[object, ...]],
) -> tuple[list, list, list]:
    """主キーを振り直し、時刻順に並べる。"""

    def renumber(rows: list[tuple[object, ...]], time_index: int) -> list:
        ordered = sorted(rows, key=lambda row: (row[time_index], row[1]))
        return [(idx + 1, *row) for idx, row in enumerate(ordered)]

    return (
        renumber(orders, 2),
        renumber(logins, 1),
        renumber(transfers, 3),
    )


def _build_case(rng: random.Random, seed: int, attempt: int) -> Case:
    users = _build_users(rng)
    template = rng.choice(TEMPLATES)

    if template.key == "admin_impersonation":
        candidates = [int(row[0]) for row in users if int(row[4]) == 0]
    else:
        candidates = [int(row[0]) for row in users]
    culprit_id = rng.choice(candidates)

    orders, logins, transfers = _GENERATORS[template.key](rng, users, culprit_id)
    orders, logins, transfers = _assign_ids(orders, logins, transfers)

    tables = {
        "users": tuple(users),
        "orders": tuple(orders),
        "logins": tuple(logins),
        "transfers": tuple(transfers),
    }
    culprit_name = next(str(row[1]) for row in users if int(row[0]) == culprit_id)
    return Case(
        seed=seed,
        attempt=attempt,
        template=template,
        culprit_id=culprit_id,
        culprit_name=culprit_name,
        tables=tables,
    )


def generate_case(seed: int, *, max_attempts: int = 100) -> Case:
    """一意解が検証できた事件を返す。検証に通らなければ別シードで再生成する。"""
    for attempt in range(max_attempts):
        rng = random.Random(f"{seed}:{attempt}")
        case = _build_case(rng, seed, attempt)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "case.db"
            case.write_db(path)
            matches = verify_case_db(path, case.template)
        if matches == [case.culprit_id]:
            return case
    raise CaseGenerationError(
        f"seed={seed} で一意解の事件を生成できませんでした（{max_attempts}回試行）。"
    )


__all__ = [
    "BASE_DATE",
    "Case",
    "CaseGenerationError",
    "CaseTemplate",
    "TEMPLATES",
    "TEMPLATES_BY_KEY",
    "find_matching_users",
    "generate_case",
    "verify_case_db",
]
