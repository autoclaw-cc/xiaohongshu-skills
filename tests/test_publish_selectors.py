"""发布页输入框定位相关单测。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from xhs.errors import PublishError
from xhs.publish import _find_content_element
from xhs.publish_long_article import _find_long_title_element
from xhs.selectors import CONTENT_EDITOR, LONG_ARTICLE_TITLE


class FakePage:
    """最小 Page 替身，仅覆盖输入框探测所需接口。"""

    def __init__(self, *, existing: set[str] | None = None, evaluate_result: str = "") -> None:
        self._existing = existing or set()
        self._evaluate_result = evaluate_result

    def has_element(self, selector: str) -> bool:
        return selector in self._existing

    def evaluate(self, _expression: str) -> str:
        return self._evaluate_result


def test_find_content_element_prefers_known_editor() -> None:
    page = FakePage(existing={CONTENT_EDITOR})
    assert _find_content_element(page) == CONTENT_EDITOR


def test_find_content_element_uses_dynamic_fallback() -> None:
    page = FakePage(evaluate_result='[data-xhs-editor-target="content"]')
    assert _find_content_element(page) == '[data-xhs-editor-target="content"]'


def test_find_content_element_raises_when_missing() -> None:
    page = FakePage()
    with pytest.raises(PublishError, match="内容输入框"):
        _find_content_element(page)


def test_find_long_title_element_prefers_known_selector() -> None:
    page = FakePage(existing={LONG_ARTICLE_TITLE})
    assert _find_long_title_element(page) == LONG_ARTICLE_TITLE


def test_find_long_title_element_uses_dynamic_fallback() -> None:
    page = FakePage(evaluate_result='[data-xhs-editor-target="long-article-title"]')
    assert _find_long_title_element(page) == '[data-xhs-editor-target="long-article-title"]'


def test_find_long_title_element_raises_when_missing() -> None:
    page = FakePage()
    with pytest.raises(PublishError, match="长文标题输入框"):
        _find_long_title_element(page)
