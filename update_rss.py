#!/usr/bin/env python3
"""Update the Imakara Nihongo RSS feed from public Podbbang magazine pages."""

from __future__ import annotations

import html
import re
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime, parsedate_to_datetime
from pathlib import Path
from typing import Iterable
from xml.sax.saxutils import escape, quoteattr

import requests
from playwright.sync_api import Error as PlaywrightError, Page, sync_playwright


MAGAZINE_ID = "1790934"
BASE_URL = f"https://www.podbbang.com/magazines/{MAGAZINE_ID}"
ISSUES_URL = f"{BASE_URL}/issues"
RSS_PATH = Path("imakara_nihongo_rss.xml")
USER_AGENT = "Mozilla/5.0 (compatible; ImakaraRSSUpdater/1.0)"
LATEST_ISSUES_TO_RECHECK = 2


@dataclass(frozen=True)
class Issue:
    issue_id: str
    title: str


@dataclass(frozen=True)
class Episode:
    episode_id: str
    title: str
    duration: str
    media_url: str
    media_length: int


def navigate(page: Page, url: str, selector: str) -> None:
    """Open a page with retries because Podbbang occasionally responds slowly."""
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            page.wait_for_selector(selector, timeout=30_000)
            return
        except PlaywrightError as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"Could not load {url}") from last_error


def fetch_issue_list(page: Page) -> list[Issue]:
    navigate(page, ISSUES_URL, ".issues-main-list")
    rows = page.locator(".issues-main-list").evaluate_all(
        """els => els.map(el => {
          const src = el.querySelector('img')?.getAttribute('src') || '';
          const match = src.match(/playlist_(\\d+)/);
          return {id: match ? match[1] : null,
                  title: (el.querySelector('b')?.textContent || '').trim()};
        }).filter(row => row.id && row.title)"""
    )
    issues = [Issue(str(row["id"]), str(row["title"])) for row in rows]
    if not issues:
        raise RuntimeError("No magazine issues were found")
    return issues


def direct_media_url(url: str) -> str:
    return re.sub(
        r"^https?://file\.ssenhosting\.com/",
        "https://cdn.podbbang.com/",
        html.unescape(url),
    )


def get_media_length(session: requests.Session, url: str) -> int:
    """Return the MP3 byte length, retrying HEAD and falling back to a range GET."""
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            response = session.head(url, timeout=30, allow_redirects=True)
            response.raise_for_status()
            length = int(response.headers.get("Content-Length", "0"))
            content_type = response.headers.get("Content-Type", "").lower()
            if length > 0 and content_type.startswith("audio/"):
                return length

            response = session.get(
                url,
                headers={"Range": "bytes=0-0"},
                timeout=30,
                allow_redirects=True,
                stream=True,
            )
            response.raise_for_status()
            content_range = response.headers.get("Content-Range", "")
            match = re.search(r"/(\d+)$", content_range)
            if match and int(match.group(1)) > 0:
                return int(match.group(1))
            raise RuntimeError(f"No usable media length in response for {url}")
        except (requests.RequestException, RuntimeError, ValueError) as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"Could not verify media file: {url}") from last_error


def fetch_episodes(page: Page, session: requests.Session, issue: Issue) -> list[Episode]:
    url = f"{ISSUES_URL}/{issue.issue_id}"
    navigate(page, url, "h1")
    page.wait_for_timeout(1_000)

    source = page.content().replace(r"\u002F", "/")
    media_urls = list(dict.fromkeys(re.findall(r'https?://[^"\'<> ]+?\.mp3', source)))
    episode_ids = re.findall(r"\{id:(\d+),magazineId:", source)
    titles = page.locator("h3").evaluate_all(
        """els => els
          .map(el => (el.textContent || '').trim())
          .filter(text => /^무료\\s*/.test(text))
          .map(text => text.replace(/^무료\\s*/, '').trim())"""
    )
    durations = page.locator("body *").evaluate_all(
        """els => els
          .filter(el => el.children.length === 0 && /^\\d\\d:\\d\\d:\\d\\d$/.test((el.textContent || '').trim()))
          .map(el => (el.textContent || '').trim())"""
    )

    count = len(media_urls)
    episode_ids = episode_ids[:count]
    titles = titles[:count]
    durations = durations[:count]
    if not count or not (len(episode_ids) == len(titles) == len(durations) == count):
        raise RuntimeError(
            f"Episode metadata mismatch for issue {issue.issue_id}: "
            f"ids={len(episode_ids)}, titles={len(titles)}, durations={len(durations)}, media={count}"
        )

    episodes: list[Episode] = []
    for episode_id, title, duration, raw_url in zip(
        episode_ids, titles, durations, media_urls, strict=True
    ):
        media_url = direct_media_url(raw_url)
        episodes.append(
            Episode(
                episode_id=str(episode_id),
                title=str(title),
                duration=str(duration),
                media_url=media_url,
                media_length=get_media_length(session, media_url),
            )
        )
    return episodes


def read_existing_feed() -> tuple[str, set[str], set[str], dict[str, datetime]]:
    if not RSS_PATH.exists():
        raise FileNotFoundError(f"Missing {RSS_PATH}")
    xml = RSS_PATH.read_text(encoding="utf-8")
    root = ET.fromstring(xml)
    channel = root.find("channel")
    if channel is None:
        raise RuntimeError("RSS channel is missing")

    guids: set[str] = set()
    issue_ids: set[str] = set()
    latest_by_issue: dict[str, datetime] = {}
    for item in channel.findall("item"):
        guid = (item.findtext("guid") or "").strip()
        if guid:
            guids.add(guid)
        link = (item.findtext("link") or "").strip()
        match = re.search(r"/issues/(\d+)/episodes/", link)
        if match:
            issue_id = match.group(1)
            issue_ids.add(issue_id)
            pub_date = (item.findtext("pubDate") or "").strip()
            if pub_date:
                parsed = parsedate_to_datetime(pub_date).astimezone(timezone.utc)
                latest_by_issue[issue_id] = max(latest_by_issue.get(issue_id, parsed), parsed)
    return xml, guids, issue_ids, latest_by_issue


def default_issue_date(issue_title: str) -> datetime:
    match = re.search(r"(\d{4})년\s*(\d{1,2})월호", issue_title)
    if not match:
        raise RuntimeError(f"Could not parse issue month: {issue_title}")
    return datetime(int(match.group(1)), int(match.group(2)), 1, 12, 0, tzinfo=timezone.utc)


def item_xml(issue: Issue, episode: Episode, published_at: datetime) -> str:
    title = escape(f"{episode.title} ({issue.title})")
    description = escape(f"이마까라 니홍고 · {issue.title}")
    episode_url = f"{ISSUES_URL}/{issue.issue_id}/episodes/{episode.episode_id}"
    guid = f"podbbang-magazine-{MAGAZINE_ID}-{episode.episode_id}"
    return (
        "    <item>\n"
        f"      <title>{title}</title>\n"
        f"      <description>{description}</description>\n"
        f"      <link>{escape(episode_url)}</link>\n"
        f"      <guid isPermaLink=\"false\">{guid}</guid>\n"
        f"      <pubDate>{format_datetime(published_at)}</pubDate>\n"
        f"      <enclosure url={quoteattr(episode.media_url)} length=\"{episode.media_length}\" type=\"audio/mpeg\"/>\n"
        f"      <itunes:duration>{escape(episode.duration)}</itunes:duration>\n"
        "      <itunes:explicit>false</itunes:explicit>\n"
        "    </item>\n"
    )


def insert_items(xml: str, blocks: Iterable[str]) -> str:
    combined = "".join(blocks)
    if not combined:
        return xml
    marker = "    <item>"
    index = xml.find(marker)
    if index < 0:
        index = xml.find("  </channel>")
    if index < 0:
        raise RuntimeError("Could not find RSS insertion point")
    updated = xml[:index] + combined + xml[index:]
    now = format_datetime(datetime.now(timezone.utc))
    return re.sub(
        r"<lastBuildDate>[^<]+</lastBuildDate>",
        f"<lastBuildDate>{now}</lastBuildDate>",
        updated,
        count=1,
    )


def main() -> int:
    xml, existing_guids, existing_issue_ids, latest_by_issue = read_existing_feed()
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    new_blocks: list[str] = []
    new_count = 0
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
        page = browser.new_page(user_agent=USER_AGENT, locale="ko-KR")
        try:
            issues = fetch_issue_list(page)
            candidate_ids = {issue.issue_id for issue in issues[:LATEST_ISSUES_TO_RECHECK]}
            candidate_ids.update(
                issue.issue_id for issue in issues if issue.issue_id not in existing_issue_ids
            )

            for issue in issues:
                if issue.issue_id not in candidate_ids:
                    continue
                episodes = fetch_episodes(page, session, issue)
                unseen = [
                    episode
                    for episode in episodes
                    if f"podbbang-magazine-{MAGAZINE_ID}-{episode.episode_id}" not in existing_guids
                ]
                if not unseen:
                    continue

                cursor = latest_by_issue.get(issue.issue_id, default_issue_date(issue.title))
                dated = []
                for episode in unseen:
                    cursor += timedelta(minutes=1)
                    dated.append((episode, cursor))
                for episode, published_at in reversed(dated):
                    new_blocks.append(item_xml(issue, episode, published_at))
                    existing_guids.add(
                        f"podbbang-magazine-{MAGAZINE_ID}-{episode.episode_id}"
                    )
                    new_count += 1
                print(f"Found {len(unseen)} new episode(s) in {issue.title}")
        finally:
            browser.close()

    if not new_blocks:
        print("No new episodes found; RSS was not changed.")
        return 0

    updated = insert_items(xml, new_blocks)
    ET.fromstring(updated)
    RSS_PATH.write_text(updated, encoding="utf-8")
    print(f"Updated RSS with {new_count} new episode(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
