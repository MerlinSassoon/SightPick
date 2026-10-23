"""DeepSeek 客户端：JSON 解析、格式归一化、错误不重试。"""

from __future__ import annotations

import json

import pytest

from recruiting_capture.errors import LLMError
from recruiting_capture.llm.deepseek_client import DeepSeekClient, _extract_json


class FakeResponse:
    def __init__(self, payload: dict, status: int = 200) -> None:
        self._payload = payload
        self.status_code = status
        self.text = json.dumps(payload, ensure_ascii=False)

    def json(self) -> dict:
        return self._payload


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = list(responses)
        self.posts = 0

    def post(self, *args, **kwargs) -> FakeResponse:
        self.posts += 1
        return self.responses.pop(0)


def _client(session: FakeSession) -> DeepSeekClient:
    return DeepSeekClient(
        api_key="test-key",
        base_url="https://api.deepseek.com",
        model="deepseek-flash",
        session=session,
        sleep=lambda seconds: None,
    )


def _response(content: str) -> FakeResponse:
    return FakeResponse({"choices": [{"message": {"content": content}}]})


def test_extract_json_plain() -> None:
    assert _extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_with_code_fence_and_text() -> None:
    text = '说明如下：\n```json\n{"result": ["a", "b"]}\n```\n以上。'
    assert _extract_json(text) == {"result": ["a", "b"]}


def test_extract_json_invalid() -> None:
    with pytest.raises(LLMError):
        _extract_json("不是 JSON")


def test_missing_api_key(monkeypatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(LLMError):
        DeepSeekClient(api_key="", session=FakeSession([]))


def test_denoise_list_format() -> None:
    session = FakeSession([_response('{"result": ["第一行", "第二行"]}')])
    client = _client(session)
    result = client.denoise(
        texts=["第一行", "第二行"],
        frame=None,
        expected_content="职位描述",
        expected_format="list",
    )
    assert result == ["第一行", "第二行"]
    assert session.posts == 1


def test_denoise_str_format() -> None:
    session = FakeSession([_response('{"result": "python开发工程师"}')])
    client = _client(session)
    result = client.denoise(
        texts=["python开发工程师"],
        frame=None,
        expected_content="岗位名称",
        expected_format="str",
    )
    assert result == "python开发工程师"


def test_confirm_anchor_parsing() -> None:
    session = FakeSession(
        [
            _response(
                '{"exists": true, "corrected_key": "职位描述", "reason": "错字", "box": [1,2,3,4]}'
            )
        ]
    )
    client = _client(session)
    confirmation = client.confirm_anchor(
        texts=["职位描迷"], frame=None, anchor="职位描述"
    )
    assert confirmation.exists is True
    assert confirmation.corrected_key == "职位描述"
    assert confirmation.box == [1, 2, 3, 4]


def test_confirm_click_parsing() -> None:
    session = FakeSession([_response('{"matched": false, "reason": "未切换"}')])
    client = _client(session)
    confirmation = client.confirm_click(texts=["岗位列表"], frame=None)
    assert confirmation.matched is False


def test_4xx_is_not_retried() -> None:
    session = FakeSession([FakeResponse({"error": "unauthorized"}, status=401)])
    client = _client(session)
    with pytest.raises(LLMError):
        client.denoise(
            texts=["x"],
            frame=None,
            expected_content="",
            expected_format="list",
        )
    assert session.posts == 1


def test_5xx_is_retried_then_fails() -> None:
    session = FakeSession(
        [
            FakeResponse({"error": "server"}, status=503),
            FakeResponse({"error": "server"}, status=503),
            FakeResponse({"error": "server"}, status=503),
        ]
    )
    client = _client(session)
    with pytest.raises(LLMError):
        client.denoise(
            texts=["x"],
            frame=None,
            expected_content="",
            expected_format="list",
        )
    assert session.posts == 3
