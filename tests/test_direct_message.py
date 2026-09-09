from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import cli
from xhs import direct_message as dm
from xhs.errors import CDPError, NotLoggedInError

USER_ID = "0123456789abcdef01234567"
NAME = "测试收件人"
CONTENT = "第一行\n第二行 ' \" \\ 😀 <script>只是文字</script>"


class FakePage:
    def __init__(self, outcome: str = "sent") -> None:
        self.outcome = outcome
        self.actions: list[str] = []
        self.state = {
            "ready": True,
            "conversation_ready": True,
            "on_chat_page": True,
            "user_id": USER_ID,
            "recipient": NAME,
            "draft": "",
            "outgoing": [],
        }
        self.navigate = Mock()
        self.wait_for_load = Mock()

    def evaluate(self, expression: str) -> dict:
        params = json.loads(expression.removeprefix(f"({dm._SCRIPT})(")[:-1])
        action = params["action"]
        self.actions.append(action)
        if action == "fill":
            if self.state["draft"] and self.state["draft"] != params["content"]:
                return {"error": "已有不同的草稿"}
            self.state["draft"] = params["content"]
            return {"filled": True}
        if action == "send":
            self.state["draft"] = ""
            if self.outcome == "disconnect":
                raise CDPError("响应丢失，但网页可能已经发送")
            if self.outcome != "timeout":
                self.state["outgoing"].append(
                    {
                        "message_id": "new-id",
                        "store_id": "3" if self.outcome == "sent" else "",
                        "text": params["content"],
                        "failed": self.outcome == "failed",
                        "pending": self.outcome == "pending",
                    }
                )
            return {"before_ids": []}
        return self.state.copy()


def test_preview_preserves_text_and_never_sends() -> None:
    page = FakePage()
    result = dm.fill_direct_message(page, USER_ID, NAME, CONTENT)
    assert result["content"] == CONTENT
    assert result["sent"] is False
    assert "send" not in page.actions


def test_different_draft_is_not_overwritten() -> None:
    page = FakePage()
    page.state["draft"] = "用户自己的草稿"
    with pytest.raises(dm.DirectMessageError, match="草稿"):
        dm.fill_direct_message(page, USER_ID, NAME, CONTENT)
    assert page.state["draft"] == "用户自己的草稿"
    assert "send" not in page.actions


def test_cannot_switch_away_from_a_draft() -> None:
    page = FakePage()
    page.state.update(user_id="another", draft="不能丢失")
    with pytest.raises(dm.DirectMessageError, match="草稿"):
        dm.fill_direct_message(page, USER_ID, NAME, CONTENT)
    page.navigate.assert_not_called()


def test_confirmation_required_before_browser_access() -> None:
    page = FakePage()
    with pytest.raises(dm.DirectMessageError, match="confirm"):
        dm.send_direct_message(page, USER_ID, NAME, CONTENT)
    assert not page.actions


@pytest.mark.parametrize(
    ("outcome", "status", "success"),
    [
        ("sent", "sent", True),
        ("failed", "failed", False),
        ("pending", "unknown", False),
        ("timeout", "unknown", False),
        ("disconnect", "unknown", False),
    ],
)
def test_send_outcomes_never_retry(outcome: str, status: str, success: bool) -> None:
    page = FakePage(outcome)
    result = dm.send_direct_message(page, USER_ID, NAME, CONTENT, confirmed=True, timeout=0)
    assert result["status"] == status
    assert result["success"] is success
    assert page.actions.count("send") == 1


@pytest.mark.parametrize("store_id", ["", "0", "-1", "not-a-number"])
def test_local_bubble_is_not_server_acknowledgement(store_id: str) -> None:
    assert not dm._is_acknowledged({"message_id": "local-uuid", "store_id": store_id})


def test_duplicate_is_rejected_before_refill_or_send() -> None:
    page = FakePage()
    page.state["outgoing"] = [{"text": CONTENT, "message_id": "old-id", "store_id": "2"}]
    with pytest.raises(dm.DirectMessageError, match="重复"):
        dm.send_direct_message(page, USER_ID, NAME, CONTENT, confirmed=True)
    assert "send" not in page.actions
    assert "fill" not in page.actions


def test_old_matching_message_cannot_verify_a_new_send(monkeypatch: pytest.MonkeyPatch) -> None:
    page = FakePage("timeout")
    original = page.evaluate

    def evaluate(expression: str) -> dict:
        result = original(expression)
        if '"action": "send"' in expression:
            page.state["outgoing"] = [{"text": CONTENT, "message_id": "old-id", "store_id": "2"}]
            return {"before_ids": ["old-id"]}
        return result

    monkeypatch.setattr(page, "evaluate", evaluate)
    result = dm.send_direct_message(page, USER_ID, NAME, CONTENT, confirmed=True, timeout=0)
    assert result["status"] == "unknown"


@pytest.mark.parametrize("content", ["", " \n ", "a" * 1001, "😀" * 501])
def test_invalid_content_rejected_before_browser_access(content: str) -> None:
    page = FakePage()
    with pytest.raises(dm.DirectMessageError):
        dm.fill_direct_message(page, USER_ID, NAME, content)
    assert not page.actions


def test_file_normalizes_bom_and_windows_newlines(tmp_path: Path) -> None:
    file = tmp_path / "message.txt"
    file.write_bytes(b"\xef\xbb\xbfhello\r\nworld\r\n")
    assert dm.read_message_file(str(file)) == "hello\nworld"
    dm.validate_content("😀" * 500)


def test_relative_file_rejected() -> None:
    with pytest.raises(dm.DirectMessageError, match="绝对路径"):
        dm.read_message_file("message.txt")


@pytest.mark.parametrize("user_id", ["nickname", "../chat", 'x";alert(1)', "0" * 25])
def test_invalid_id_rejected_before_navigation(user_id: str) -> None:
    page = FakePage()
    with pytest.raises(dm.DirectMessageError, match="用户 ID"):
        dm.fill_direct_message(page, user_id, NAME, CONTENT)
    assert not page.actions


def test_login_and_js_errors_are_not_treated_as_ready() -> None:
    page = Mock()
    page.evaluate.return_value = {"not_logged_in": True}
    with pytest.raises(NotLoggedInError):
        dm.list_conversations(page)
    page.evaluate.return_value = {"__xhs_error": "页面脚本异常"}
    with pytest.raises(dm.DirectMessageError, match="页面脚本异常"):
        dm.list_conversations(page)


def test_cli_rejects_unconfirmed_send_without_connecting(monkeypatch: pytest.MonkeyPatch) -> None:
    connect = Mock()
    monkeypatch.setattr(cli, "_connect", connect)
    args = cli.build_parser().parse_args(
        [
            "send-direct-message",
            "--user-id",
            USER_ID,
            "--expected-name",
            NAME,
            "--content-file",
            "/nonexistent/message.txt",
        ]
    )
    with pytest.raises(dm.DirectMessageError, match="confirm"):
        args.func(args)
    connect.assert_not_called()
