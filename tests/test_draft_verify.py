"""Offline save/list/reopen control-flow tests; no browser or account is used."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from xhs.errors import PublishError
from xhs.publish import save_as_draft


class Page:
    def __init__(self, *, matches=None, actual=None, images_loaded=True, edit_index=1):
        self.calls = []
        self.expected = dict(title="test", body="body", tags=["tag"], images=6)
        self.actual = actual
        self.matches = [{"id": "draft-1"}] if matches is None else matches
        self.images_loaded = images_loaded
        self.edit_index = edit_index
        self.snapshots = 0

    def evaluate(self, expression):
        if "const el=document.querySelector" in expression:
            self.snapshots += 1
            return self.expected if self.snapshots == 1 or self.actual is None else self.actual
        if "草稿存储于当前使用的浏览器本地" in expression:
            return self.matches
        if "const buttons = Array.from" in expression:
            return self.edit_index
        if "naturalWidth" in expression:
            assert "imgs.length === 6" in expression
            return self.images_loaded
        if expression.startswith("document.querySelector"):
            return self.expected["title"]
        raise AssertionError(expression)

    def _call(self, method, params):
        self.calls.append((method, params))
        return dict(x=100, y=200) if method == "click_save_draft" else None


@pytest.fixture(autouse=True)
def fast_time():
    # Each loop receives one iteration, then times out. No real sleeping or 20s spins.
    with (
        patch("xhs.publish.time.sleep"),
        patch("xhs.publish.time.monotonic", side_effect=range(0, 1000, 10)),
    ):
        yield


def test_save_then_unique_list_then_reopen():
    page = Page()
    result = save_as_draft(page, verify=True)
    assert result["reopened"] and result["images_loaded"]
    assert result["body_matches"] and result["tags_match"]
    assert result["storage"] == "current_browser_local"
    assert not result["publish"]
    assert [m for m, _ in page.calls] == ["click_save_draft", "click_nth_element"]
    assert "draft-1" in page.calls[1][1]["selector"]
    # Explicitly locate 编辑; it need not be the first action (which may be 删除).
    assert page.calls[1][1]["index"] == 1


def test_default_preserves_list_and_does_not_require_images():
    page = Page()
    page.expected["images"] = 0
    result = save_as_draft(page)
    assert result["list_confirmed"] and not result["reopened"]
    assert [m for m, _ in page.calls] == ["click_save_draft"]
    assert page.snapshots == 0


@pytest.mark.parametrize("verify", [False, True])
@pytest.mark.parametrize("matches", [[], [{"id": "a"}, {"id": "b"}], [{"id": None}]])
def test_missing_ambiguous_or_invalid_list_does_not_retry(verify, matches):
    page = Page(matches=matches)
    with pytest.raises(PublishError):
        save_as_draft(page, verify=verify)
    assert [m for m, _ in page.calls] == ["click_save_draft"]


@pytest.mark.parametrize("field,value", [("title", ""), ("images", 0)])
def test_invalid_verify_form_is_rejected_before_click(field, value):
    page = Page()
    page.expected[field] = value
    with pytest.raises(PublishError):
        save_as_draft(page, verify=True)
    assert page.calls == []


@pytest.mark.parametrize(
    "field,value", [("title", "changed"), ("body", "changed"), ("tags", ["other"]), ("images", 5)]
)
def test_reopened_mismatch_never_reports_success_or_resaves(field, value):
    actual = dict(title="test", body="body", tags=["tag"], images=6)
    actual[field] = value
    page = Page(actual=actual)
    with pytest.raises(PublishError, match="回读内容不一致"):
        save_as_draft(page, verify=True)
    assert len(page.calls) == 2


def test_image_load_failure_never_reports_success():
    page = Page(images_loaded=False)
    with pytest.raises(PublishError, match="配图未全部加载"):
        save_as_draft(page, verify=True)
    assert len(page.calls) == 2


def test_ambiguous_edit_action_does_not_click_or_resave():
    page = Page(edit_index=None)
    with pytest.raises(PublishError, match="编辑入口"):
        save_as_draft(page, verify=True)
    assert [m for m, _ in page.calls] == ["click_save_draft"]
