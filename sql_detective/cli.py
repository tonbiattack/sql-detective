"""最小プレイ可能UI（CLI）と事件検証（Issue #5）。

``sql-detective play``   1事件を通しで遊ぶ
``sql-detective debug``  開発用モードで事件を調べる
``sql-detective verify`` 生成事件N件の一意解チェック
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import unicodedata
from pathlib import Path

from .case import (
    TEMPLATES,
    CaseGenerationError,
    generate_case,
    verify_case_db,
)
from .db import TABLE_COLUMNS, TABLE_ORDER, QueryRejected, QueryTimeout
from .game import MAX_QUERIES, Game, LossReason, Phase

# --- 表示 -------------------------------------------------------------------

RULE = "─" * 68


def _print_header() -> None:
    print(RULE)
    print("  SQL探偵：ログの死角")
    print(RULE)


def _print_schema() -> None:
    print("\n【利用できるテーブル】")
    for table in TABLE_ORDER:
        columns = ", ".join(TABLE_COLUMNS[table])
        print(f"  {table:<10} ({columns})")
    print("\n  書き込みはできません。SELECT のみ実行できます。")


def _print_brief(game: Game) -> None:
    case = game.case
    print(f"\n【依頼】{case.template.title}")
    print(f"  {case.template.brief}")
    print(f"\n  捜査コスト: {game.max_queries}回 / 1クエリ {100}ms・{100}行まで")
    print("  LIMIT なしの全件取得はコスト2倍。告発は無料。")


def _format_cell(value: object) -> str:
    if value is None:
        return "NULL"
    return str(value)


def _display_width(text: str) -> int:
    """端末上の表示幅（全角文字を2として数える）。"""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _display_width(text))


def _print_result(result, index: int) -> None:
    print(f"\n── 結果 #{index} ── {result.elapsed_ms:.1f}ms / コスト{result.cost}")
    if not result.columns:
        print("  (列なし)")
        return
    widths = [_display_width(col) for col in result.columns]
    body = [[_format_cell(v) for v in row] for row in result.rows]
    for row in body:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], _display_width(cell))
    header = "  " + " | ".join(
        _pad(col, widths[i]) for i, col in enumerate(result.columns)
    )
    print(header)
    print("  " + "-+-".join("-" * w for w in widths))
    for row in body:
        print("  " + " | ".join(_pad(cell, widths[i]) for i, cell in enumerate(row)))
    if result.truncated:
        print(f"  ※ 先頭{len(result.rows)}行のみ表示しています。")
    if not result.rows:
        print("  (該当なし)")


def _print_status(game: Game) -> None:
    print(
        f"\n  残り捜査コスト: {game.budget_left}/{game.max_queries}"
        f"  （使用 {game.queries_used}）"
    )


def _print_ending(game: Game) -> None:
    case = game.case
    print("\n" + RULE)
    if game.phase is Phase.WON:
        print("  【解決】告発は的中した。")
        print(f"  犯人: {case.culprit_name}（ID {case.culprit_id}）")
        print(f"  スコア: {game.score}（残り捜査コスト）")
    else:
        if game.loss_reason is LossReason.WRONG_ACCUSATION:
            print("  【敗北】告発は外れた。")
        else:
            print("  【敗北】捜査コストを使い切った。")
        print(f"  真犯人: {case.culprit_name}（ID {case.culprit_id}）")
        print(f"  依頼: {case.template.brief}")
    print(RULE)


# --- 開発用コマンド ---------------------------------------------------------


def _print_debug_help() -> None:
    print("\n【デバッグコマンド】")
    print("  :status       シード・事件・状態と捜査履歴を表示")
    print("  :answer       犯人と一意性検証SQLを表示（ネタバレ）")
    print("  :verify       現在のDBで一意解を検証（ネタバレ）")
    print("  :budget N     残り捜査コストを N に変更（0〜初期値）")
    print("  :reset        同じ事件を初期状態に戻す（履歴も消去）")
    print("  :help         この一覧を表示")
    print("  終了後も :reset で同じ事件を再開できます。空行で終了。")


def _debug_command(game: Game, line: str) -> None:
    """明示的にデバッグモードを選んだ場合だけ呼び出す。"""
    parts = line.split()
    command = parts[0].lower()
    if command == ":help" and len(parts) == 1:
        _print_debug_help()
    elif command == ":status" and len(parts) == 1:
        case = game.case
        print(f"  シード: {case.seed} / 生成再試行: {case.attempt}")
        print(f"  事件: {case.template.title} ({case.template.key})")
        print(f"  状態: {game.phase.value} / 敗北理由: {game.loss_reason.value if game.loss_reason else 'なし'}")
        print(f"  捜査コスト: 残り {game.budget_left}/{game.max_queries} / 使用 {game.queries_used}")
        print(f"  捜査履歴: {len(game.history)}件")
        for index, result in enumerate(game.history, 1):
            print(f"    {index}. コスト{result.cost} / {result.row_count}行 / {result.elapsed_ms:.1f}ms: {result.sql}")
    elif command == ":answer" and len(parts) == 1:
        case = game.case
        print(f"  【ネタバレ】犯人: {case.culprit_name}（ID {case.culprit_id}）")
        print("  一意性検証SQL:")
        print(case.template.predicate_sql.strip())
    elif command == ":verify" and len(parts) == 1:
        try:
            matches = verify_case_db(game.db_path, game.case.template)
        except (QueryRejected, QueryTimeout, OSError) as exc:
            print(f"  一意性検証に失敗しました: {exc}")
            return
        valid = matches == [game.case.culprit_id]
        print(f"  【ネタバレ】検証結果: {'一意' if valid else '不一致'} / 候補ID: {matches} / 犯人ID: {game.case.culprit_id}")
    elif command == ":budget":
        if len(parts) != 2:
            print("  使い方: :budget N（0〜初期コスト）")
            return
        try:
            remaining = int(parts[1])
        except ValueError:
            print("  残りコストには整数を指定してください。")
            return
        if not 0 <= remaining <= game.max_queries:
            print(f"  残りコストは0〜{game.max_queries}で指定してください。")
            return
        if game.is_over:
            print("  終了後は :reset で再開してから変更してください。")
            return
        game.queries_used = game.max_queries - remaining
        print(f"  残り捜査コストを {remaining} に変更しました。履歴はそのままです。")
    elif command == ":reset" and len(parts) == 1:
        game.queries_used = 0
        game.history.clear()
        game.phase = Phase.BRIEFING
        game.loss_reason = None
        print(f"  シード {game.case.seed} の同じ事件を初期状態に戻しました。")
        _print_brief(game)
    else:
        print("  不明なデバッグコマンドです。:help で一覧を表示します。")


# --- play -------------------------------------------------------------------


def _read_line(prompt: str) -> str:
    try:
        return input(prompt)
    except EOFError:
        return ""


def _play(args: argparse.Namespace) -> int:
    debug = args.command == "debug"
    _print_header()
    if debug:
        print("  【デバッグモード】答えや検証SQLを表示できます。")
    try:
        game = Game.new(seed=args.seed)
    except CaseGenerationError as exc:
        print(f"事件の生成に失敗しました: {exc}", file=sys.stderr)
        return 1

    with game:
        _print_brief(game)
        _print_schema()
        print("\n  クエリを入力（空行で終了 / 'accuse' で告発 / 'help' で説明 / 'hint' でヒント）")
        if debug:
            print(f"  シード: {game.case.seed}（再現用） / :help でデバッグコマンド一覧")

        while debug or not game.is_over:
            _print_status(game)
            if game.is_over:
                print("  事件は終了しています。:reset で再開できます。")
            elif not game.can_investigate:
                print("  ※ 捜査コストは残っていません。告発してください。")
            line = _read_line("\nsql> ").strip()

            if not line:
                print("  終了します。")
                return 0
            if debug and line.startswith(":"):
                _debug_command(game, line)
                continue
            if game.is_over:
                print("  事件は終了しています。:reset で再開してください。")
                continue
            if line.lower() in ("help", "?", "\\h"):
                _print_schema()
                continue
            if line.lower() in ("hint", "h", "ヒント"):
                print(f"  ヒント: {game.case.template.hint}")
                continue
            if line.lower() in ("accuse", "a", "告発"):
                answer = _read_line("  告発する容疑者（ID または氏名）: ").strip()
                if not answer:
                    print("  告発をキャンセルしました。")
                    continue
                result = game.accuse(answer)
                _print_ending(game)
                if not result.correct:
                    print(f"  あなたの告発: {result.accused}")
                if debug:
                    continue
                return 0

            try:
                result = game.investigate(line)
            except (QueryRejected, QueryTimeout) as exc:
                print(f"  ✗ {exc}")
                continue
            except RuntimeError as exc:
                print(f"  ✗ {exc}")
                _print_ending(game)
                if debug:
                    continue
                return 0

            _print_result(result, len(game.history))

        _print_ending(game)
    return 0


# --- verify -----------------------------------------------------------------


def _verify(args: argparse.Namespace) -> int:
    count = args.count
    print(f"事件 {count} 件の一意解チェックを実行します…")
    failures: list[str] = []
    template_counts: dict[str, int] = {t.key: 0 for t in TEMPLATES}

    with tempfile.TemporaryDirectory() as tmp:
        for seed in range(1, count + 1):
            try:
                case = generate_case(seed)
            except CaseGenerationError as exc:
                failures.append(f"seed={seed}: 生成失敗 ({exc})")
                continue
            template_counts[case.template.key] += 1
            path = Path(tmp) / f"verify-{seed}.db"
            case.write_db(path)
            matches = verify_case_db(path, case.template)
            if matches != [case.culprit_id]:
                failures.append(
                    f"seed={seed} ({case.template.key}): "
                    f"条件を満たすのが {matches} で一意でない"
                    f"（犯人は {case.culprit_id}）"
                )

    print("\n【事件タイプの分布】")
    for key, n in template_counts.items():
        print(f"  {key:<22} {n:>4} 件")

    if failures:
        print(f"\n✗ {len(failures)} 件が一意解チェックに失敗しました:")
        for line in failures[:20]:
            print(f"  - {line}")
        if len(failures) > 20:
            print(f"  … ほか {len(failures) - 20} 件")
        return 1

    print(f"\n✓ {count} 件すべてで解が一意でした。")
    return 0


# --- エントリポイント -------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sql-detective",
        description="SQL探偵：ログの死角 — ランダム生成ログDBで犯人を当てる推理ゲーム",
    )
    sub = parser.add_subparsers(dest="command")

    play = sub.add_parser("play", help="1事件を通しで遊ぶ")
    play.add_argument(
        "--seed", type=int, default=None, help="事件のシード（省略時はランダム）"
    )
    play.set_defaults(func=_play)

    debug = sub.add_parser("debug", help="開発用モードで事件を調べる（ネタバレあり）")
    debug.add_argument(
        "--seed", type=int, default=None, help="再現する事件のシード（省略時はランダム）"
    )
    debug.set_defaults(func=_play)

    verify = sub.add_parser("verify", help="生成事件の一意解チェック")
    verify.add_argument(
        "--count", type=int, default=100, help="検証する事件数（既定: 100）"
    )
    verify.set_defaults(func=_verify)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
