# SQL探偵：ログの死角（sql-detective）

ランダム生成ログDBで犯人を当てる推理ゲーム。

> ステータス: **MVP実装済み**（CLIで1事件を通しで遊べる）

## コンセプト

毎回ランダム生成される注文／ログイン／送金ログのDBから、JOINと集計で容疑者を絞り、犯人を1人告発する。不完全な情報を読むことがそのまま推理になる。

## 遊び方

1. 依頼文が提示される（例: 深夜に不自然な送金がある）
2. クエリを実行して容疑者を絞る（実行回数が捜査コスト）
3. 容疑者を1人に絞れたら「告発」で判定
4. 当たれば残りクエリ数がスコア、外せば即終了

## セットアップ

Python 3.11+ が必要。実行時依存はなし（標準ライブラリのみ）。

```powershell
python -m pip install -e .
```

## 使い方

### 遊ぶ

```powershell
python -m sql_detective.cli play
```

シードを固定すると同じ事件を再現できる。

```powershell
python -m sql_detective.cli play --seed 3
```

CLI内の操作:

| 入力 | 動作 |
|---|---|
| `SELECT ...` | クエリを実行（捜査コストを消費） |
| `accuse` | 告発モードに入る（無料） |
| `help` | テーブル一覧を表示 |
| 空行 | 終了 |

### 事件の一意解チェック

```powershell
python -m sql_detective.cli verify --count 100
```

生成した事件N件すべてで「条件を満たすのが1人だけ」を検証する。

### テスト

```powershell
python -m pytest
```

## ルール

- 実行できるのは `SELECT`（と `WITH`）のみ。DBは読み取り専用で開く
- 1クエリ 100ms / 取得上限 100行
- 捜査コストは8回。`LIMIT` なしの全件取得はコスト2倍
- 告発は無料。外すと即終了

## 仕様書

詳細は [docs/SPEC.md](docs/SPEC.md) を参照。

## MVP範囲

- 1プレイ完結型の最小コアループのみ
- DBは SQLite
- オンライン対戦・ランキングは対象外

## 技術スタック

- 言語: Python 3.11+
- DB: SQLite（読み取り専用接続）
- UI: CLI
- テスト: pytest

## 構成

| ファイル | 役割 |
|---|---|
| `sql_detective/db.py` | 読み取り専用クエリ実行基盤（SELECT限定・上限・コスト計測） |
| `sql_detective/case.py` | 事件生成（一意解の逆算生成と検証） |
| `sql_detective/game.py` | コアループ（捜査→告発→判定・スコア） |
| `sql_detective/cli.py` | CLI UI と `verify` サブコマンド |

## Issue方針

- #1 [Epic/仕様確定] MVP仕様を確定する
- #2 基盤（DB実行基盤）
- #3 ロジック（コアループ実装）
- #4 データ（生成）
- #5 UI/検証（最小UIとプレイ検証）

各Issueは「目的・やること・受け入れ条件・対象外」で管理する。
