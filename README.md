# tanbunism
tanbunismは「知識を単文と関係に分解して管理する」ための知識管理システムです。
現在は開発中です。

## 動機
大学数学や読書を通して、知識同士の依存関係を追跡しながら整理したいと考えた。

## 説明

本ツールは
1. 理解した内容を1文とそれらを関連付けに分解してメモ(再構成)するための独自文法
2. その文法に則ったプレーンテキストの読み取りと蓄積
3. 関連付けによる情報の検索・表示

を提供する。

これで知識を整理すれば、概念の位置づけや複雑さなどを一目で分かるようになる。

## Requirements
Python 3.11+
## How to use
### Installation
    pip install tanbunism
### プレーンテキストの独自文法
```md
# 題名 // 情報のまとまりの識別として使う
    // メタ情報
    [@author 著者]
    [@publish 第一出版日]
    [@url url]
! コメントは!から始まる1行
## 見出し1
    aaa //1文にはインデントが必要
    bbb\
        ccc //改行を含めて1文(bbbccc)と見なす
### 見出し2
    ...
###### 見出し5 // 5段階まで見出しが使える
    ppp
        qqq    //pppの配下を表す 詳細などを書く
        <- rrr //pppの前提
        -> sss //pppによる帰結

    ...途中
```

### CLI
```sh
tb --help #helpの表示
```

読書メモをDBへ送らずに検査する:

```sh
# 1ファイル
tb check notes/book.kn

# ディレクトリ内の.tbと.knを再帰検査
tb check notes

# エラー位置、該当行、原因、修正案も表示
tb check -v notes

# Markdownを対象にする
tb check notes --extension md
```

検査に失敗したファイルがある場合は終了コード`1`を返す。パーサーは処理を
続けられないため、1回の検査では各ファイルの最初のエラーを検出する。通常は
エラーがあるファイルのパスだけを列挙し、`-v`で詳細を表示する。

### Database execution deadlines

Neo4jの実行期限は`tanbun/config/database.py`で共通設定する。APIのGET・HEAD・
OPTIONSは30秒、それ以外は120秒が初期値。環境変数で変更できる（正の有限値のみ）。

```text
NEO4J_READ_TIMEOUT_SECONDS=30
NEO4J_WRITE_TIMEOUT_SECONDS=120
NEO4J_SCHEMA_TIMEOUT_SECONDS=300
```

通常のCypher・ORM・明示トランザクションに適用し、HTTP処理ではなくDB側で
期限超過のトランザクションを終了させる。期限はトランザクション全体に対する
上限で、複数クエリを実行してもリセットしない。APIでは504を返し、自動再試行
しない。暗黙トランザクションは各クエリに上限が適用されるため、バックグラウンド
ジョブ全体の終了期限ではない。API以外の既定値はREAD側の設定を使う。

`task start`は起動前に`task schema-install`を実行する。スキーマ登録だけは
`NEO4J_SCHEMA_TIMEOUT_SECONDS`を使う（各DBトランザクションの上限）。
一意制約は`IF NOT EXISTS`で再実行可能にし、登録後にラベル・プロパティ・
制約の種類を確認する。同名の別制約や未完成のインデックスを成功扱いにせず、
同じイベントループ内の制約登録は共通ロックで直列化する。並列登録のデッドロックだけは
冪等なDDLを最大3回再試行する。
自動削除もしない。異常時は`SHOW INDEXES`と`SHOW CONSTRAINTS`で確認して
手動修復する。DB切断などで構築が中断された場合の残存まで完全には防げない。

Auraの`SHOW TRANSACTIONS`の`metaData`に`app`と`operation`が表示される。
期限設定は新しく開始するトランザクションに適用され、既存の暴走処理は別途停止が必要。

### PageRankの一括再計算

adminの「PageRank」で対象Resourceを選び、DBに保存済みのグラフから再計算する。
ファイルの再importは不要。専用のNeo4jキューに受付を保存し、Webのlifespanで
起動するworkerが処理する。ジョブ内はResourceごとに逐次実行し、成功・失敗・
現在の対象を保存する。完了時は依頼したadminへ一覧通知とWeb Pushを送る。
失敗は残りを止めず、管理画面からそのResourceだけ再計算対象に追加できる。

- 初期値は同時実行1、待機＋実行の受付10件、1 Resourceにつき20,000ノード／
  100,000辺。adminで調整可能。512MB環境では同時実行1から増やさないことを推奨。
- import・クイズ準備とは別の制限。受付・取得は一意な調停ノードへの書込ロックで
  全Webプロセスを通じて制御する。制約・インデックスは`task schema-install`で登録。
- ジョブに120秒のリースを付け、20秒ごとに更新する。再起動・切断後は期限切れを
  再取得し、Resource単位の保存位置から再開する。完了直前の再起動では同じResourceを
  再計算する場合があるが、値の上書きだけなのでXPの重複加算はない。
- Freeホストが停止中は実行できない。次回起動後に再開する。通知は完了後の送信を
  試みるが、通知送信中の停止・配信失敗についてはキュー結果を確認すること。

計算v1はResource内の現行Sentence・Quotermを頂点とし、RESOLVED／REFは
参照先へ、TOは逆向き（前提へ）に票を流す。自己辺・重複辺・リソースを跨ぐ辺・
階層／並び順の辺は除く。[NetworkX PageRank](https://networkx.org/documentation/stable/reference/algorithms/generated/networkx.algorithms.link_analysis.pagerank_alg.pagerank.html)
をalpha=0.85、最大100反復、tol=1e-8で実行し、収束しなければ失敗として記録する。
結果はSentenceの`pagerank`（生値）と`pagerank_score`（生値×全頂点数）に保存し、
Resourceには計算バージョン・日時・元内容hash・元更新日時を保存する。

復習設定で「PageRank（知識）」を選ぶと、知識TLで未閲覧・復習間隔も考慮しながら
PageRankを推薦の重みに使う。Power・XPの計算とは独立。未計算／import後の古い
キャッシュは従来の関連数スコアに戻す。クイズ推薦はバランス方式のまま。
今日の推薦セットは固定なので、すぐ反映したい場合は設定の「今日を作り直す」を使う。

### Web Push

Web Pushを有効にするサーバーではVAPID鍵を一度だけ生成する。

```sh
poetry run vapid --gen
poetry run vapid --applicationServerKey --private-key private_key.pem
```

サーバーへ次の環境変数を設定する。秘密鍵ファイルはGitへ追加せず、Renderでは
Secret Fileなどで配置する。

```text
VAPID_PUBLIC_KEY=<applicationServerKeyの出力>
VAPID_PRIVATE_KEY=/etc/secrets/private_key.pem
VAPID_SUBJECT=mailto:<運用連絡先メールアドレス>
```

公開鍵と秘密鍵の組を変更すると既存の端末購読は使えなくなるため、同じ鍵を維持する。

例: プレーンテキストを読み取り
```sh
cat xxx.txt |tb read
# or
tb read xxx.txt
```

## URLs
PyPI: https://pypi.org/project/tanbunism/
GitHub: https://github.com/shogogoto/tanbunism
