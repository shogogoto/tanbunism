# Tanbun Language Server

Tanbunの読書メモをパースし、構文エラーと意味エラーをLSP diagnosticsとして返す。
Neo4jやWeb APIには依存しない。

## 起動

```console
tb lsp
```

Language Server Protocolの標準入出力通信なので、通常は直接実行せずエディタから起動する。

## Neovim

```lua
vim.filetype.add({ extension = { tb = "tanbun", kn = "tanbun" } })

vim.api.nvim_create_autocmd("FileType", {
  pattern = "tanbun",
  callback = function()
    vim.lsp.start({
      name = "tanbun",
      cmd = { "tb", "lsp" },
      root_dir = vim.fs.root(0, { ".git" }) or vim.fn.getcwd(),
    })
  end,
})
```

現時点ではdiagnostics、文書内用語のcompletion、definition、referencesを提供する。
`{...}`の用語埋め込みとbacktickのquotermで、用語名・同義名・aliasを補完できる。
同じ参照上でNeovimの`gd`などを実行すると、まず同一文書、次にworkspace内の
`.tb`/`.kn`ファイルから用語定義を探して移動できる。定義移動はNeo4jやWeb APIに
依存しない。
用語の定義または参照上でNeovimの`gr`相当のreferences操作を実行すると、
用語埋め込みと引用用語をworkspace全体から探す。検索後には種類別件数と所要時間を
通知する。定義移動でも検索件数と所要時間を通知する。
標準LSPのreferences応答にはグループ見出しがないため、LSPでは種類順に連続して返す。
Web APIでは種類ごとのグループ構造を返す。
起動時と文書のopen・save時に、解析状態、所要時間、行数、用語数、ノード数、関係数をクライアントへ通知する。
Quick Fix、アップロードは後続機能とする。

## 共通言語サービス

診断、補完、定義検索の本体は`language_service`に置き、LSPはそのadapterとして扱う。
Web editorも`/language/analysis`、`/language/completion`、`/language/definition`、
`/language/references`から同じ処理を利用でき、これらのendpointはNeo4jに依存しない。
