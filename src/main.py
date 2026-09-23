"""Collect industry updates and publish new matches to Feishu."""

from __future__ import annotations

import json
import sys
from pathlib import Path


SRC = Path(__file__).resolve().parent
ROOT = SRC.parent
sys.path.insert(0, str(SRC))

from radar import (  # noqa: E402
    collect_feed,
    github_search,
    build_message,
    load_keywords,
    load_seen,
    matches_keywords,
    save_seen,
    select_new,
    send_to_feishu,
)


FEEDS_FILE = ROOT / "config" / "feeds.json"
GITHUB_QUERIES_FILE = ROOT / "config" / "github_queries.json"
KEYWORDS_FILE = ROOT / "config" / "keywords.txt"
SEEN_FILE = ROOT / "data" / "seen.json"


def main() -> None:
    feeds = json.loads(FEEDS_FILE.read_text(encoding="utf-8"))
    github_queries = json.loads(GITHUB_QUERIES_FILE.read_text(encoding="utf-8"))
    keywords = load_keywords(KEYWORDS_FILE)
    seen = load_seen(SEEN_FILE)
    entries = []

    for feed in feeds:
        try:
            feed_entries = collect_feed(feed)
            entries.extend(feed_entries)
            print(f"{feed['name']}: {len(feed_entries)} 条")
        except Exception as exc:
            print(f"读取 RSS 失败：{feed['name']}: {exc}")

    for query in github_queries:
        try:
            repo_entries = github_search(query)
            entries.extend(repo_entries)
            print(f"{query['name']}: {len(repo_entries)} 个项目")
        except Exception as exc:
            print(f"读取 GitHub Search 失败：{query['name']}: {exc}")

    matched = [entry for entry in entries if matches_keywords(entry, keywords)]
    selected = select_new(matched, seen)

    if not selected:
        print("没有新的匹配信息")
        return

    message = build_message(selected)
    send_to_feishu(message)
    save_seen(SEEN_FILE, seen.union(entry.link for entry in selected))
    print(f"已推送 {len(selected)} 条信息")


if __name__ == "__main__":
    main()
