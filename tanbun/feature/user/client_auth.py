"""CLIの認証ヘッダー。サーバールーターやDB設定を読み込まない."""

from tanbun.config import LocalConfig
from tanbun.config.env import ClientSettings
from tanbun.feature.user.errors import TokenUnsavedError


def auth_header() -> dict:
    """接続先が一致する保存済みトークンだけを利用する."""
    config = LocalConfig.load()
    if not config.CREDENTIALS:
        msg = "認証情報が取得できませんでした"
        raise TokenUnsavedError(msg)
    if isinstance(config.CREDENTIALS_URL, str) and config.CREDENTIALS_URL.rstrip(
        "/",
    ) != ClientSettings().KNOWDE_URL.rstrip("/"):
        msg = (
            "保存した認証情報の接続先が違います。"
            "現在のKNOWDE_URLでログインし直してください。"
        )
        raise TokenUnsavedError(msg)
    return {"Authorization": f"Bearer {config.CREDENTIALS['access_token']}"}
