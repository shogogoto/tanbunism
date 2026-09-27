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

例: プレーンテキストを読み取り
```sh
cat xxx.txt |tb read
# or
tb read xxx.txt
```

## URLs
PyPI: https://pypi.org/project/tanbunism/
GitHub: https://github.com/shogogoto/tanbunism
