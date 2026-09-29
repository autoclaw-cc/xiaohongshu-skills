from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from xhs.errors import PublishError
from xhs.publish import (
    _assert_topics_preserved,
    _input_single_tag,
    _input_tags,
    publish_image_content,
)


class FakePage:
    def __init__(
        self,
        committed_after_click: list[str] | None = None,
        commit_sequence: list[str] | None = None,
        suggestion_available: bool = True,
        click_effective: bool = True,
        semantic_click: bool = True,
        suggestion_text: str | None = None,
    ) -> None:
        self.committed_after_click = committed_after_click
        self.commit_sequence = commit_sequence or []
        self.committed: list[str] = []
        self.suggestion_available = suggestion_available
        self.click_effective = click_effective
        self.semantic_click = semantic_click
        self.suggestion_text = suggestion_text
        self.clicked = False
        self.click_count = 0
        self.typed: list[str] = []
        self.pressed_keys: list[str] = []
        self.current_tag = ""
        self.wrapped: list[str] = []
        self.evaluate_scripts: list[str] = []

    def evaluate(self, script: str):
        self.evaluate_scripts.append(script)
        if "['mousedown', 'mouseup', 'click']" in script:
            self.click_element("matched-topic-name")
            return True
        if 'querySelectorAll("p").length' in script:
            return 1
        if "candidateHtml" in script:
            committed_html = "".join(f"<a>#{tag}</a> " for tag in self.committed)
            wrapped_html = "".join(f"<span>#{tag}</span> " for tag in self.wrapped)
            handled = self.current_tag in self.committed or self.current_tag in self.wrapped
            raw_html = "" if handled else f"#{self.current_tag}"
            candidates = [f"<a>#{tag}</a>" for tag in self.committed if tag == self.current_tag]
            return {
                "html": f"<p>{committed_html}{wrapped_html}{raw_html}</p>",
                "text": "".join(self.typed),
                "candidateHtml": candidates,
            }
        if "return index + 1" in script:
            text = self.suggestion_text or f"#{self.current_tag}"
            return 1 if text.strip().lstrip("#").strip() == self.current_tag else 0
        if "filter(topic => topic && topic.id && topic.name)" in script:
            return [{"name": tag, "id": f"id-{tag}"} for tag in self.committed]
        return True

    def type_text(self, text: str, delay_ms: int = 50) -> None:
        self.typed.append(text)
        if text == "#" or text == " ":
            self.current_tag = ""
        else:
            self.current_tag += text

    def has_element(self, selector: str) -> bool:
        return self.suggestion_available

    def click_element(self, selector: str) -> None:
        self.clicked = True
        if self.click_effective:
            if self.commit_sequence and self.click_count < len(self.commit_sequence):
                clicked_tag = self.commit_sequence[self.click_count]
            elif self.committed_after_click:
                clicked_tag = self.committed_after_click[0].lstrip("#")
            else:
                clicked_tag = self.current_tag
            target = self.committed if self.semantic_click else self.wrapped
            target.append(clicked_tag)
        self.click_count += 1

    def press_key(self, key: str) -> None:
        self.pressed_keys.append(key)
        if key == "Enter":
            self.click_element("keyboard-selection")

    def click_element_by_text(self, selector: str, text: str) -> None:
        self.click_element(selector)


class PublishTagTests(unittest.TestCase):
    @patch("xhs.publish.time.sleep", return_value=None)
    def test_input_single_tag_commits_then_separates(self, _sleep) -> None:
        page = FakePage(["#AI研究"])

        _input_single_tag(page, "div.ql-editor", "AI研究")

        self.assertTrue(page.clicked)
        self.assertEqual("".join(page.typed), "#AI研究 ")
        self.assertEqual(page.pressed_keys, [])
        focus_calls = [
            script for script in page.evaluate_scripts if "range.collapse(false)" in script
        ]
        self.assertEqual(len(focus_calls), 2)

    @patch("xhs.publish.time.sleep", return_value=None)
    def test_input_single_tag_raises_when_suggestion_is_missing(self, _sleep) -> None:
        values = iter([0.0, 30.0])

        def fake_monotonic() -> float:
            return next(values, 30.0)

        page = FakePage([], suggestion_available=False)
        with (
            patch("xhs.publish.time.monotonic", side_effect=fake_monotonic),
            self.assertRaisesRegex(PublishError, "未找到匹配的标签联想"),
        ):
            _input_single_tag(page, "div.ql-editor", "AI研究")

    @patch("xhs.publish.time.sleep", return_value=None)
    def test_input_single_tag_raises_when_click_does_not_commit(self, _sleep) -> None:
        values = iter([0.0, 0.0, 0.0, 0.0, 30.0])

        def fake_monotonic() -> float:
            return next(values, 30.0)

        page = FakePage(click_effective=False)
        with (
            patch("xhs.publish.time.monotonic", side_effect=fake_monotonic),
            self.assertRaisesRegex(PublishError, "标签联想点击未生效"),
        ):
            _input_single_tag(page, "div.ql-editor", "AI研究")

    @patch("xhs.publish.time.sleep", return_value=None)
    def test_input_single_tag_rejects_stale_suggestion(self, _sleep) -> None:
        values = iter([0.0, 0.0, 30.0])

        def fake_monotonic() -> float:
            return next(values, 30.0)

        page = FakePage(suggestion_text="#旧标签")
        with (
            patch("xhs.publish.time.monotonic", side_effect=fake_monotonic),
            self.assertRaisesRegex(PublishError, "未找到匹配的标签联想"),
        ):
            _input_single_tag(page, "div.ql-editor", "AI研究")

    @patch("xhs.publish.time.sleep", return_value=None)
    def test_input_single_tag_rejects_longer_topic_for_short_tag(self, _sleep) -> None:
        values = iter([0.0, 0.0, 30.0])

        def fake_monotonic() -> float:
            return next(values, 30.0)

        page = FakePage(suggestion_text="#AI研究")
        with (
            patch("xhs.publish.time.monotonic", side_effect=fake_monotonic),
            self.assertRaisesRegex(PublishError, "未找到匹配的标签联想"),
        ):
            _input_single_tag(page, "div.ql-editor", "AI")

    @patch("xhs.publish.time.sleep", return_value=None)
    def test_input_single_tag_rejects_plain_span_wrapper(self, _sleep) -> None:
        page = FakePage(semantic_click=False)
        values = iter([0.0, 0.0, 0.0, 0.0, 30.0])
        with (
            patch("xhs.publish.time.monotonic", side_effect=lambda: next(values, 30.0)),
            self.assertRaises(PublishError),
        ):
            _input_single_tag(page, "div.ql-editor", "AI研究")

    @patch("xhs.publish.time.sleep", return_value=None)
    def test_input_tags_commits_five_topics_in_order(self, _sleep) -> None:
        tags = ["AI研究", "人工智能", "数学", "科研工具", "求职成长"]
        page = FakePage(commit_sequence=tags)

        _input_tags(page, "div.ql-editor", tags)

        self.assertEqual(page.click_count, 5)
        self.assertEqual(page.committed, tags)
        self.assertEqual("".join(page.typed), "".join(f"#{tag} " for tag in tags))
        focus_calls = [
            script for script in page.evaluate_scripts if "range.collapse(false)" in script
        ]
        self.assertEqual(len(focus_calls), 11)

    def test_final_gate_rejects_lost_earlier_topic(self) -> None:
        page = FakePage()
        page.committed = ["求职成长"]
        with self.assertRaisesRegex(PublishError, "缺失或重复"):
            _assert_topics_preserved(page, "div.ql-editor", ["AI研究", "求职成长"])

    def test_final_gate_rejects_duplicate_topic(self) -> None:
        page = FakePage()
        page.committed = ["数学", "数学"]
        with self.assertRaisesRegex(PublishError, "缺失或重复"):
            _assert_topics_preserved(page, "div.ql-editor", ["数学"])

    def test_failed_fill_never_clicks_publish(self) -> None:
        with (
            patch("xhs.publish.fill_publish_form", side_effect=PublishError("标签失败")),
            patch("xhs.publish.click_publish_button") as click,
            self.assertRaises(PublishError),
        ):
            publish_image_content(FakePage(), None)
        click.assert_not_called()


if __name__ == "__main__":
    unittest.main()
