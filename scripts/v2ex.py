"""V2EX API 2.0 搜索源：全站最新主题、节点主题、关键词过滤。

Token 从 ~/.hermes/.env 的 V2EX_PAT 读取。API 文档: https://edge.v2ex.com/help/api
注意: V2EX API 2.0 无全文搜索接口，search 是对最新/节点主题列表的关键词过滤。

用法:
  uv run python scripts/v2ex.py latest [--pages 2]
  uv run python scripts/v2ex.py node python [--pages 2]
  uv run python scripts/v2ex.py search 关键词 [--pages 3] [--node python]
  uv run python scripts/v2ex.py topic 1256953
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path
from typing import Any

API_BASE = "https://edge.v2ex.com/api/v2"
ENV_PATH = Path.home() / ".hermes" / ".env"
HTTP_TIMEOUT = 20


class V2exError(Exception):
    pass


def load_token() -> str:
    token = os.environ.get("V2EX_PAT", "")
    if not token and ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            if line.startswith("V2EX_PAT="):
                token = line.split("=", 1)[1].strip()
    if not token:
        raise V2exError("V2EX_PAT 未配置（~/.hermes/.env 或环境变量）")
    return token


def api_get(path: str, token: str) -> Any:
    req = urllib.request.Request(
        f"{API_BASE}/{path}",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        raise V2exError(f"请求 {path} 失败: {exc}") from exc
    if not data.get("success"):
        raise V2exError(f"API 返回失败: {data.get('message')}")
    return data.get("result")


def fetch_topics(path: str, token: str, pages: int) -> list[dict[str, Any]]:
    topics: list[dict[str, Any]] = []
    for page in range(1, pages + 1):
        result = api_get(f"{path}?p={page}", token)
        if isinstance(result, dict):
            result = result.get("result", [])
        if not result:
            break
        topics.extend(result)
    return topics


def trim(topic: dict[str, Any]) -> dict[str, Any]:
    member = topic.get("member") or {}
    node = topic.get("node") or {}
    return {
        "id": topic.get("id"),
        "title": topic.get("title"),
        "author": member.get("username"),
        "node": node.get("name"),
        "created": topic.get("created"),
        "url": topic.get("url"),
        "replies": topic.get("replies"),
        "excerpt": (topic.get("content") or "")[:200],
    }


def print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description="V2EX API 2.0 搜索源")
    parser.add_argument("action", choices=["latest", "node", "search", "topic"])
    parser.add_argument("query", nargs="?", help="节点名 / 关键词 / 主题 ID")
    parser.add_argument("--pages", type=int, default=1, help="拉取页数")
    parser.add_argument("--node", help="search 时限定节点")
    args = parser.parse_args()

    token = load_token()
    if args.action == "latest":
        topics = fetch_topics("topics/latest", token, args.pages)
    elif args.action == "node":
        if not args.query:
            parser.error("node 需要节点名，如: node python")
        topics = fetch_topics(f"nodes/{args.query}/topics", token, args.pages)
    elif args.action == "search":
        if not args.query:
            parser.error("search 需要关键词，如: search 代理")
        path = f"nodes/{args.node}/topics" if args.node else "topics/latest"
        topics = [
            t
            for t in fetch_topics(path, token, args.pages)
            if args.query.lower() in (t.get("title") or "").lower()
            or args.query.lower() in (t.get("content") or "").lower()
        ]
    else:
        if not args.query:
            parser.error("topic 需要主题 ID，如: topic 1256953")
        print_json(trim(api_get(f"topics/{args.query}", token)))
        return 0
    print_json({"count": len(topics), "topics": [trim(t) for t in topics]})
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except V2exError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        sys.exit(2)
