"""离线检查；API 响应是测试数据，不访问真实账号。"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

spec = importlib.util.spec_from_file_location(
    "v2ex", Path(__file__).resolve().parents[1] / "scripts" / "v2ex.py"
)
v2ex = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v2ex)


def test_search_and_pagination(monkeypatch, capsys):
    topic = {"id": 1, "title": "Python", "created": 10, "last_modified": 20}
    get = MagicMock(side_effect=[[topic, {"id": 2, "content": "PYTHON"}], []])
    monkeypatch.setattr(v2ex, "api_get", get)
    monkeypatch.setattr(v2ex, "load_token", lambda: "test-only")
    monkeypatch.setattr("sys.argv", ["v2ex.py", "search", "python", "--pages", "3"])
    assert v2ex.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["count"] == 2
    assert result["topics"][0]["created"] == 10
    assert get.call_count == 2
    assert get.call_args_list[1].args[0] == "topics/latest?p=2"


def test_api_failure(monkeypatch):
    response = MagicMock()
    response.__enter__.return_value.read.return_value = (
        b'{"success": false, "message": "Token expired"}'
    )
    monkeypatch.setattr(v2ex.urllib.request, "urlopen", lambda *a, **kw: response)
    with pytest.raises(v2ex.V2exError, match="Token expired"):
        v2ex.api_get("topics/latest", "test-only")
