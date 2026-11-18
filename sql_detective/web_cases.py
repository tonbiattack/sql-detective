"""SQLiteで検証した事件を、静的Web版用の証拠カードに変換する。

カードごとの候補集合は、必ず表示するSQLの結果から作る。最終カードの
候補が既存CLIの検証SQLと一致しない事件は配布しない。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import tempfile
import textwrap
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from .case import Case, generate_case, verify_case_db


@dataclass(frozen=True)
class Evidence:
    title: str
    description: str
    sql: str


EVIDENCE: dict[str, tuple[Evidence, ...]] = {
    "midnight_transfer": (
        Evidence(
            "深夜の送金記録",
            "0時から5時より前の送金を調べます。金額はまだ絞りません。",
            """SELECT t.from_user_id AS user_id, u.name AS 氏名,
                      t.created_at AS 日時, t.amount AS 金額
               FROM transfers t JOIN users u ON u.id = t.from_user_id
               WHERE time(t.created_at) >= '00:00:00' AND time(t.created_at) < '05:00:00'
               ORDER BY t.created_at, t.id""",
        ),
        Evidence(
            "深夜かつ高額の送金",
            "同じ送金が深夜かつ50万円以上かを確かめます。",
            """SELECT t.from_user_id AS user_id, u.name AS 氏名,
                      t.created_at AS 日時, t.amount AS 金額
               FROM transfers t JOIN users u ON u.id = t.from_user_id
               WHERE time(t.created_at) >= '00:00:00' AND time(t.created_at) < '05:00:00'
                 AND t.amount >= 500000
               ORDER BY t.created_at, t.id""",
        ),
    ),
    "login_bruteforce": (
        Evidence(
            "失敗回数の多い社員",
            "接続元を区別せず、失敗が3回以上ある社員を調べます。",
            """SELECT l.user_id, u.name AS 氏名, COUNT(*) AS 失敗回数
               FROM logins l JOIN users u ON u.id = l.user_id
               WHERE l.success = 0 GROUP BY l.user_id, u.name
               HAVING COUNT(*) >= 3 ORDER BY l.user_id""",
        ),
        Evidence(
            "接続元ごとの失敗回数",
            "同じ社員・同じIPの失敗回数をまとめて調べます。",
            """SELECT l.user_id, u.name AS 氏名, l.ip AS 接続元IP, COUNT(*) AS 失敗回数
               FROM logins l JOIN users u ON u.id = l.user_id
               WHERE l.success = 0 GROUP BY l.user_id, u.name, l.ip
               HAVING COUNT(*) >= 3 ORDER BY l.user_id, l.ip""",
        ),
    ),
    "bulk_orders": (
        Evidence(
            "高額注文の合計件数",
            "日付を区別せず、10万円以上の注文が3件以上ある社員を調べます。",
            """SELECT o.user_id, u.name AS 氏名, COUNT(*) AS 注文件数
               FROM orders o JOIN users u ON u.id = o.user_id
               WHERE o.amount >= 100000 GROUP BY o.user_id, u.name
               HAVING COUNT(*) >= 3 ORDER BY o.user_id""",
        ),
        Evidence(
            "日付ごとの高額注文",
            "同じ社員・同じ日付の高額注文をまとめて調べます。",
            """SELECT o.user_id, u.name AS 氏名, date(o.created_at) AS 注文日, COUNT(*) AS 注文件数
               FROM orders o JOIN users u ON u.id = o.user_id
               WHERE o.amount >= 100000 GROUP BY o.user_id, u.name, date(o.created_at)
               HAVING COUNT(*) >= 3 ORDER BY o.user_id, 注文日""",
        ),
    ),
    "admin_impersonation": (
        Evidence(
            "高額送金の送金者",
            "30万円以上の送金を調べます。送り手・受け手の役割はまだ絞りません。",
            """SELECT t.from_user_id AS user_id, s.name AS 送金者, r.name AS 受取人,
                      t.amount AS 金額, s.is_admin AS 送金者管理者, r.is_admin AS 受取人管理者
               FROM transfers t JOIN users s ON s.id = t.from_user_id
               JOIN users r ON r.id = t.to_user_id
               WHERE t.amount >= 300000 ORDER BY t.id""",
        ),
        Evidence(
            "管理者宛ての高額送金",
            "同じ送金の受取人が管理者か確かめます。管理者は1、一般社員は0です。",
            """SELECT t.from_user_id AS user_id, s.name AS 送金者, r.name AS 受取人,
                      t.amount AS 金額, s.is_admin AS 送金者管理者, r.is_admin AS 受取人管理者
               FROM transfers t JOIN users s ON s.id = t.from_user_id
               JOIN users r ON r.id = t.to_user_id
               WHERE t.amount >= 300000 AND r.is_admin = 1 ORDER BY t.id""",
        ),
        Evidence(
            "一般社員からの送金",
            "管理者宛て高額送金のうち、送り手が一般社員の記録を確認します。",
            """SELECT t.from_user_id AS user_id, s.name AS 送金者, r.name AS 受取人,
                      t.amount AS 金額, s.is_admin AS 送金者管理者, r.is_admin AS 受取人管理者
               FROM transfers t JOIN users s ON s.id = t.from_user_id
               JOIN users r ON r.id = t.to_user_id
               WHERE t.amount >= 300000 AND r.is_admin = 1 AND s.is_admin = 0 ORDER BY t.id""",
        ),
    ),
}

# 表示条件はCLIの検証SQLと一致する。曖昧な「昨夜」「連続」は用いない。
BRIEFS = {
    "midnight_transfer": "記録期間中、0時から5時より前に50万円以上を送金した社員は誰でしょう？",
    "login_bruteforce": "記録期間中、同じIPから3回以上ログインに失敗した社員は誰でしょう？",
    "bulk_orders": "同じ日に10万円以上の注文を3件以上出した社員は誰でしょう？ 注文状態は問いません。",
    "admin_impersonation": "一般社員のうち、管理者へ30万円以上を送金した社員は誰でしょう？",
}


def export_case(case: Case, db_path: Path) -> dict:
    """事件DBから全証拠を作成し、候補集合の整合性を検証する。"""
    expected = verify_case_db(db_path, case.template)
    if expected != [case.culprit_id]:
        raise ValueError(f"seed={case.seed}: 一意解が正しくありません: {expected}")
    users = [
        {"id": row[0], "name": row[1], "department": row[2]}
        for row in case.users
    ]
    all_ids = {user["id"] for user in users}
    cards = []
    previous = all_ids
    uri = db_path.resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as db:
        for index, definition in enumerate(EVIDENCE[case.template.key]):
            sql = textwrap.dedent(definition.sql).strip()
            cursor = db.execute(sql)
            columns = [column[0] for column in cursor.description]
            rows = [list(row) for row in cursor.fetchall()]
            candidates = sorted({int(row[0]) for row in rows})
            if not set(candidates) <= previous or case.culprit_id not in candidates:
                raise ValueError(f"seed={case.seed}: 証拠{index + 1}の候補が不整合です")
            previous = set(candidates)
            cards.append({
                "id": f"evidence-{index + 1}",
                "title": definition.title,
                "description": definition.description,
                "sql": sql,
                "columns": columns,
                "rows": rows,
                "candidateIds": candidates,
            })
    if previous != {case.culprit_id}:
        raise ValueError(f"seed={case.seed}: 証拠から正解が一意に決まりません")
    return {
        "seed": case.seed,
        "type": case.template.key,
        "title": case.template.title,
        "brief": BRIEFS[case.template.key],
        "suspects": users,
        "evidence": cards,
    }


def build_cases(count: int = 100, start_seed: int = 1) -> dict:
    if count <= 0:
        raise ValueError("事件数は1以上にしてください")
    cases = []
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "case.db"
        for seed in range(start_seed, start_seed + count):
            case = generate_case(seed)
            case.write_db(path)
            cases.append(export_case(case, path))
    return {"version": 1, "cases": cases}


def main() -> None:
    parser = argparse.ArgumentParser(description="Web版用の検証済み事件を生成する")
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--output", type=Path, default=Path("web/cases.json"))
    args = parser.parse_args()
    data = build_cases(args.count)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"{len(data['cases'])}件の検証済み事件を {args.output} に出力しました。")


if __name__ == "__main__":
    main()
