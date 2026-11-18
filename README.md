# SQL探偵：ログの死角

**ランダム生成されたログDBから、証拠をたどって犯人を1人告発する推理ゲーム。**

[![Deploy](https://github.com/tonbiattack/sql-detective/actions/workflows/pages.yml/badge.svg)](https://github.com/tonbiattack/sql-detective/actions/workflows/pages.yml)
[![Python](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-229%20passed-brightgreen)](tests)

> **ブラウザで遊ぶ:** https://tonbiattack.github.io/sql-detective/

注文・ログイン・送金のログから、条件に合う人物を絞り込み、真犯人を当てます。
証拠を開くたびに候補が減り、少ない証拠で当てるほど高得点。SQLを書かなくても遊べます。

---

## 特徴

- **毎回ちがう事件** — シードからログDBを生成。同じシードなら同じ事件を再現できる
- **解は必ず1人** — 生成のたびに検証クエリで一意性を確認し、複数解の事件は出さない
- **2つの遊び方** — ブラウザで証拠を選ぶモードと、CLIでSQLを書くモード
- **サーバー不要** — 静的ファイルだけで動く。GitHub Pagesで公開

## 遊び方

### ブラウザ版

[公開URL](https://tonbiattack.github.io/sql-detective/) を開くだけです。インストールもログインも不要。

1. 事件の概要と容疑者10人が表示される
2. 証拠カードを開くと、実際のSQLの実行結果が表で表示される
3. 条件に合わない容疑者は自動で絞り込まれる
4. 犯人だと思う人物を選んで告発

証拠の初回閲覧は1枚につき1コスト。正解時は最大100点から閲覧1枚につき20点減点、誤告発は0点です。

### CLI版（SQLを書いて遊ぶ）

```bash
python -m pip install -e .
python -m sql_detective.cli play
```

シードを固定すると同じ事件を再現できます。

```bash
python -m sql_detective.cli play --seed 3
```

| 入力 | 動作 |
|---|---|
| `SELECT ...` | クエリを実行（捜査コストを消費） |
| `accuse` | 告発モードに入る（無料） |
| `hint` | ヒントを表示 |
| `help` | テーブル一覧を表示 |
| 空行 | 終了 |

**ルール**

- 実行できるのは `SELECT`（と `WITH`）のみ。DBは読み取り専用で開く
- 1クエリ 100ms / 取得上限 100行
- 捜査コストは8回。`LIMIT` なしの全件取得はコスト2倍
- 告発は無料。外すと即終了

### デバッグモード

```bash
python -m sql_detective.cli debug --seed 3
```

開発用に、シード・状態・捜査履歴の確認、犯人と検証SQLの表示、一意性検証、残りコストの変更、同一事件のリセットができます。通常の `play` では使えません。

| 入力 | 動作 |
|---|---|
| `:status` | シード、事件、状態、捜査履歴を表示 |
| `:answer` | 犯人と検証SQLを表示（ネタバレ） |
| `:verify` | 現在のDBで一意性を検証（ネタバレ） |
| `:budget N` | 残り捜査コストを変更 |
| `:reset` | 同じ事件を初期状態に戻す |

## 仕組み

### 事件生成

固定4テーブル（`users` / `orders` / `logins` / `transfers`）に、犯人が一意になるようデータを**逆算生成**します。生成後は必ず検証クエリを実行し、条件を満たすのが1人だけであることを確認。満たさなければ別シードで再生成します。

| 事件 | 決め手 |
|---|---|
| 深夜の送金 | 時刻と金額の複合条件 |
| 同一IPからの連続失敗 | `user_id` × `ip` のグループ集計 |
| 高額注文の連続 | `user_id` × 日付のグループ集計 |
| 管理者への不自然な送金 | `users` への2回JOIN |

各事件には**ニアミス**（条件を1つだけ外す容疑者）を混ぜています。条件を1つ落とすと複数人出るため、絞り込みが推理になります。

### ブラウザ版のデータ

Python側で事件を生成・検証し、証拠カードのSQLを実際に実行した結果を `web/cases.json` に書き出します。ブラウザはそのJSONを読むだけで、事件生成もDB実行もしません。

答えはJSONに含めません。決め手の証拠が示す唯一の候補から導くため、データとロジックが食い違えばテストが落ちます。

## 開発

```bash
# セットアップ
python -m pip install -e .

# Pythonのテスト
python -m pytest

# ブラウザ版のロジックテスト
node web/logic.test.mjs

# 事件データの再生成
python -m sql_detective.web_cases --count 100

# ローカルでブラウザ版を確認
python -m http.server 8765 --directory web
```

ブラウザの実機確認（ヘッドレスChrome/Edgeで通しプレイ・スマホ幅・Consoleエラーを検証）:

```bash
node web/browser.test.mjs http://127.0.0.1:8765/index.html
```

### 構成

| パス | 役割 |
|---|---|
| `sql_detective/db.py` | 読み取り専用クエリ実行基盤（SELECT限定・上限・コスト計測） |
| `sql_detective/case.py` | 事件生成（一意解の逆算生成と検証） |
| `sql_detective/game.py` | コアループ（捜査→告発→判定・スコア） |
| `sql_detective/cli.py` | CLI UI と `verify` / `debug` サブコマンド |
| `sql_detective/web_cases.py` | ブラウザ版の証拠データ生成 |
| `web/` | ブラウザ版（HTML/CSS/JS・事件データ） |
| `tests/` | pytest によるテスト |

### デプロイ

`main` に `web/` または `sql_detective/` の変更が入ると、GitHub Actions が検証してから GitHub Pages へ公開します。検証では pytest、事件データの再生成差分チェック、JSロジックテストを実行し、いずれかが失敗するとデプロイしません。

初回のみ、リポジトリの **Settings → Pages → Source** を **GitHub Actions** に設定してください。

## 仕様

詳細は [docs/SPEC.md](docs/SPEC.md) を参照してください。

## コントリビュート

Issue と Pull Request を歓迎します。

1. 変更は小さく保ち、既存のテストを通してください
2. 事件生成を変えたら `python -m sql_detective.web_cases --count 100` でデータを再生成してください
3. 一意性が壊れていないことを `python -m sql_detective.cli verify --count 100` で確認してください

## ライセンス

[MIT](LICENSE)
