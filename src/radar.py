"""Pure collection, filtering, deduplication, and Feishu formatting helpers."""

from __future__ import annotations

import html
import json
import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TIMEOUT = 30
MAX_SEEN_LINKS = 5000
MAX_MESSAGE_LENGTH = 3800


@dataclass(frozen=True)
class Entry:
    title: str
    summary: str
    link: str
    category: str
    source: str
    published: datetime | None = None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(node: ET.Element, names: set[str]) -> ET.Element | None:
    for child in list(node):
        if _local_name(child.tag) in names:
            return child
    return None


def _text(node: ET.Element | None) -> str:
    if node is None:
        return ""
    return " ".join("".join(node.itertext()).split())


def clean_text(value: str, limit: int = 220) -> str:
    value = html.unescape(value or "")
    value = re.sub(r"<[^>]+>", " ", value)
    value = " ".join(value.split())
    return value[:limit]


def parse_datetime(value: str) -> datetime | None:
    if not value:
        return None

    try:
        result = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def is_recent(value: str, days: int = 7) -> bool:
    published = parse_datetime(value)
    if published is None:
        return True
    return published >= datetime.now(timezone.utc) - timedelta(days=days)


def fetch_bytes(url: str, headers: dict[str, str] | None = None) -> bytes:
    request_headers = {
        "User-Agent": "audio-industry-radar/1.0 (+https://github.com/byteghost2026/audio-industry-radar)",
    }
    if headers:
        request_headers.update(headers)

    request = urllib.request.Request(url, headers=request_headers)
    with urllib.request.urlopen(request, timeout=DEFAULT_TIMEOUT) as response:
        return response.read()


def parse_feed(xml_bytes: bytes, source: str, category: str) -> list[Entry]:
    root = ET.fromstring(xml_bytes)
    nodes = [
        node for node in root.iter()
        if _local_name(node.tag) in {"item", "entry"}
    ]
    entries: list[Entry] = []

    for node in nodes:
        title = _text(_child(node, {"title"}))
        summary_node = _child(node, {"description", "summary", "content", "encoded"})
        date_node = _child(node, {"pubDate", "published", "updated", "date"})
        link_node = _child(node, {"link"})
        link = ""
        if link_node is not None:
            link = link_node.attrib.get("href", "") or _text(link_node)

        published_text = _text(date_node)
        if not title or not link or not is_recent(published_text):
            continue

        entries.append(
            Entry(
                title=clean_text(title, 240),
                summary=clean_text(_text(summary_node)),
                link=link.strip(),
                category=category,
                source=source,
                published=parse_datetime(published_text),
            )
        )

    return entries


def collect_feed(feed: dict) -> list[Entry]:
    return parse_feed(
        fetch_bytes(feed["url"]),
        source=feed["name"],
        category=feed.get("category", "其他"),
    )


def github_search(query: dict, days: int = 30) -> list[Entry]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    search_query = f"{query['query']} pushed:>={cutoff}"
    url = "https://api.github.com/search/repositories?" + urllib.parse.urlencode(
        {
            "q": search_query,
            "sort": "updated",
            "order": "desc",
            "per_page": "8",
        }
    )
    payload = json.loads(
        fetch_bytes(
            url,
            headers={
                "Accept": "application/vnd.github+json",
            },
        )
    )

    entries: list[Entry] = []
    for item in payload.get("items", []):
        name = item.get("full_name", "")
        description = clean_text(item.get("description", ""))
        topics = ", ".join(item.get("topics", []))
        searchable = f"{name} {description} {topics}"
        entries.append(
            Entry(
                title=f"{name} ★{item.get('stargazers_count', 0)}",
                summary=description or "GitHub 项目最近有更新",
                link=item.get("html_url", ""),
                category=query.get("category", "GitHub项目"),
                source=query.get("name", "GitHub Search"),
                published=parse_datetime(item.get("pushed_at", "")),
            )
        )

    return entries


def load_keywords(path: Path) -> list[str]:
    return [
        line.strip().lower()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def matches_keywords(entry: Entry, keywords: list[str]) -> bool:
    if not keywords:
        return True
    content = f"{entry.title} {entry.summary}".lower()
    return any(keyword in content for keyword in keywords)


def load_seen(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return set(json.loads(path.read_text(encoding="utf-8")))


def save_seen(path: Path, seen: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(sorted(seen)[-MAX_SEEN_LINKS:], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def select_new(entries: list[Entry], seen: set[str], limit: int = 12) -> list[Entry]:
    selected: list[Entry] = []
    current_links: set[str] = set()

    entries.sort(
        key=lambda entry: entry.published or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )

    for entry in entries:
        if not entry.link or entry.link in seen or entry.link in current_links:
            continue
        current_links.add(entry.link)
        selected.append(entry)
        if len(selected) >= limit:
            break

    return selected


def build_message(entries: list[Entry]) -> str:
    today = datetime.now().astimezone().strftime("%Y-%m-%d")
    lines = [f"【音频与智能硬件行业动态｜{today}】", ""]

    for index, entry in enumerate(entries, 1):
        lines.extend(
            [
                f"{index}. [{entry.category}] {entry.title}",
                f"来源：{entry.source}",
            ]
        )
        if entry.summary:
            lines.append(f"摘要：{entry.summary}")
        lines.extend([f"链接：{entry.link}", ""])

    message = "\n".join(lines).strip()
    if len(message) > MAX_MESSAGE_LENGTH:
        message = message[: MAX_MESSAGE_LENGTH - 30].rstrip() + "\n\n（内容较多，请查看原文链接）"
    return message


def send_to_feishu(message: str, webhook: str | None = None) -> None:
    webhook = webhook or os.environ.get("FEISHU_WEBHOOK", "").strip()
    if not webhook:
        raise RuntimeError("FEISHU_WEBHOOK is not configured")

    payload = json.dumps(
        {
            "msg_type": "text",
            "content": {"text": message},
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        webhook,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=DEFAULT_TIMEOUT) as response:
        body = response.read().decode("utf-8")
        if response.status != 200:
            raise RuntimeError(f"Feishu HTTP error: {response.status}: {body}")

    result = json.loads(body)
    if result.get("code", 0) != 0:
        raise RuntimeError(f"Feishu API error: {body}")
