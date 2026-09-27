"""Tanbun言語サービスAPIのテスト."""

from collections.abc import Generator

import pytest
from fastapi import FastAPI, status
from fastapi.testclient import TestClient

from tanbun.feature.language_service.router import language_router


@pytest.fixture
def client() -> Generator[TestClient]:
    """終了時にworker threadも閉じるHTTP client.

    Yields:
        テスト対象APIのclient。

    """
    app = FastAPI()
    app.include_router(language_router())
    with TestClient(app) as test_client:
        yield test_client


def test_analysis_exposes_common_diagnostics(client: TestClient) -> None:
    """Webにも共通の人向け診断を返す."""
    response = client.post("/language/analysis", json={"text": "invalid\n"})

    assert response.status_code == status.HTTP_200_OK
    result = response.json()
    assert result["diagnostics"][0]["code"] == "missing-title"
    assert result["statistics"]["line_count"] == 1


def test_definition_exposes_same_document_location(client: TestClient) -> None:
    """Webにも文書内の定義位置を返す."""
    text = "# title\n  身体化: 説明\n  {身体化}\n"
    response = client.post(
        "/language/definition",
        json={"text": text, "position": {"line": 2, "character": 4}},
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "line": 1,
        "start_character": 2,
        "end_character": 5,
    }


def test_completion_exposes_candidates_and_replace_range(client: TestClient) -> None:
    """Webにも用語補完候補と置換範囲を返す."""
    text = "# title\n  身体化: 説明\n  文中の{身"
    response = client.post(
        "/language/completion",
        json={"text": text, "position": {"line": 2, "character": 7}},
    )

    assert response.status_code == status.HTTP_200_OK
    result = response.json()
    assert result["replace_range"] == {
        "line": 2,
        "start_character": 6,
        "end_character": 7,
    }
    assert result["symbols"] == [{"label": "身体化", "detail": "用語"}]
    assert result["closing"] == "}"


def test_references_preserve_reference_kind(client: TestClient) -> None:
    """Web APIでも参照記法の種類を失わない."""
    text = "# title\n  用語: 説明\n  {用語}\n  対象\n    <- `用語`\n"
    response = client.post(
        "/language/references",
        json={"text": text, "position": {"line": 1, "character": 3}},
    )

    assert response.status_code == status.HTTP_200_OK
    groups = response.json()
    assert [item["kind"] for item in groups] == [
        "embedded-term",
        "quoterm",
    ]
    assert [len(item["references"]) for item in groups] == [1, 1]
