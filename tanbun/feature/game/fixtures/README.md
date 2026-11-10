# フロントエンドとのゲーム設定契約

`frontend_game_contract.json` は tanbunism-web の `e44d832` から採取した
テスト用データ。本番データや認証情報は含まない。

- `catalog`: `prepareSkillCatalog()` → `requestGameCatalog(true, value)` が
  実際に生成する管理APIのPUT本文。
- `skill_file`: 同じカタログから `skillFileText()` が生成するversion 6のJSON。
- 初期カタログと減速・毒の例を入力にし、攻撃・HP/MP回復・バフ・デバフ・
  状態異常・歩数回復を含めた。バックエンドのモデルを正解のJSONとして
  テスト実行時に生成し直さず、この消費者側の出力を固定して検証する。

通常のpytest/CIは固定データを実APIへ送り、保存・再読込・スキル取り込み・
統合取り込みプレビューを確認する。最新フロントとの照合も行う場合は、
そのcheckoutに通常の依存パッケージをインストールした上で実行する。

```sh
TANBUNISM_WEB_ROOT=/path/to/tanbunism-web poetry run pytest -q -x --tb=short \
  tanbun/feature/game/test_frontend_contract.py
```

フロントのcheckoutがない通常CIでは、最新TSとの照合1件だけをskipする。
固定JSONのAPI検証はskipしない。フロント側のCIと自動連携した仕組みではない。

出力を意図的に変更した場合の再採取:

```sh
node scripts/capture_frontend_game_contract.mjs /path/to/tanbunism-web \
  < tanbun/feature/game/fixtures/frontend_game_contract.json
```

標準出力を確認し、差分をレビューしてからfixtureを更新する。入力と同じ
ファイルへ直接リダイレクトしない。スクリプトは既存のesbuildを使って
実フロントの関数をメモリ内で実行し、fetchを捕捉する。ビルド出力の保存・
HTTP通信・DB接続・フロントのファイル変更は行わない。
