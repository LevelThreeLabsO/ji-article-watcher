#!/usr/bin/env python3
"""
Single-shot poller: checks the Jewish Insider WordPress API for newly published
posts and sends each one to Slack. Designed to run once per invocation from a
GitHub Actions cron (see .github/workflows/poll.yml) — no loop, no sleep.

State (the IDs already posted) lives in watcher_state.json, which the workflow
commits back to the repo after each run so the next run remembers what it sent.

Reads the Slack webhook from the SLACK_WEBHOOK_URL environment variable, which
the workflow supplies from a repository secret.
"""

import html
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")

API_URL = (
    "https://jewishinsider.com/wp-json/wp/v2/posts"
    "?per_page=20&orderby=date&order=desc"
    "&_fields=id,date,link,title,excerpt"
)
STATE_FILE = Path(__file__).parent / "watcher_state.json"
EXCERPT_MAX_CHARS = 320
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


def clean_html(raw):
    text = BeautifulSoup(raw or "", "html.parser").get_text(" ", strip=True)
    return " ".join(html.unescape(text).split())


def clean_excerpt(raw):
    text = clean_html(raw)
    for tail in ("Read More", "Continue Reading"):
        if text.endswith(tail):
            text = text[: -len(tail)].rstrip()
    text = text.rstrip(" .…")
    if len(text) > EXCERPT_MAX_CHARS:
        text = text[:EXCERPT_MAX_CHARS].rsplit(" ", 1)[0] + "…"
    elif text:
        text += "…"
    return text


def format_time(date_str):
    """'2026-06-09T10:32:19' -> '10:32 AM ET' (the site clock is Eastern)."""
    try:
        return datetime.fromisoformat(date_str).strftime("%-I:%M %p ET")
    except (ValueError, TypeError):
        return ""


def fetch_posts():
    resp = requests.get(API_URL, headers={"User-Agent": USER_AGENT}, timeout=30)
    resp.raise_for_status()
    return [
        {
            "id": p["id"],
            "title": clean_html(p.get("title", {}).get("rendered", "")),
            "link": p.get("link", ""),
            "excerpt": clean_excerpt(p.get("excerpt", {}).get("rendered", "")),
            "time": format_time(p.get("date", "")),
        }
        for p in resp.json()
    ]


def load_seen():
    if STATE_FILE.exists():
        return set(json.loads(STATE_FILE.read_text()))
    return set()


def save_seen(seen):
    STATE_FILE.write_text(json.dumps(sorted(seen, reverse=True)[:500]))


def slack_escape(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def post_to_slack(text):
    resp = requests.post(WEBHOOK_URL, json={"text": text}, timeout=15)
    resp.raise_for_status()


def article_message(post):
    title = slack_escape(post["title"]) or "(untitled)"
    dateline = f"  ·  {post['time']}" if post["time"] else ""
    lines = [
        ":newspaper: *New on Jewish Insider:*",
        f"*<{post['link']}|{title}>*{dateline}",
    ]
    if post["excerpt"]:
        lines.append(slack_escape(post["excerpt"]))
    return "\n".join(lines)


def main():
    if not WEBHOOK_URL:
        print("ERROR: SLACK_WEBHOOK_URL env var is not set.", file=sys.stderr)
        sys.exit(1)

    seen = load_seen()
    posts = fetch_posts()
    new_posts = [p for p in posts if p["id"] not in seen]

    if not STATE_FILE.exists():
        # First ever run — baseline silently, just say hello once.
        save_seen({p["id"] for p in posts})
        post_to_slack(
            ":eyes: Article watcher is live (running 24/7 on GitHub Actions). "
            "New Jewish Insider posts will appear here as they publish."
        )
        print(f"First run — baseline of {len(posts)} posts saved.")
        return

    if not new_posts:
        print(f"No new posts ({len(posts)} on feed).")
        return

    # Oldest-first so Slack reads in publish order.
    for post in reversed(new_posts):
        post_to_slack(article_message(post))
        seen.add(post["id"])
    save_seen(seen)
    print(f"Posted {len(new_posts)} new article(s).")


if __name__ == "__main__":
    main()
