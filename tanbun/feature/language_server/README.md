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
vim.filetype.add({ extension = { kn = "tanbun" } })

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

現時点ではdiagnosticsのみを提供する。補完、定義移動、Quick Fix、アップロードは後続機能とする。
