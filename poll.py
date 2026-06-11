#!/usr/bin/env python3
"""
Single-shot poller: checks the Jewish Insider WordPress API for newly published
posts and sends each new article's URL to Slack, letting Slack unfurl it into a
rich preview card on its own. Designed to run once per invocation from the
GitHub Actions pinger (see .github/workflows/poll.yml) — no loop, no sleep.

State (the IDs already posted) lives in watcher_state.json, which the workflow
commits back to the repo after each run so the next run remembers what it sent.

Reads the Slack webhook from the SLACK_WEBHOOK_URL environment variable, which
the workflow supplies from a repository secret.
"""

import json
import os
import sys
from pathlib import Path

import requests

WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")

API_URL = (
    "https://jewishinsider.com/wp-json/wp/v2/posts"
    "?per_page=20&orderby=date&order=desc&_fields=id,link"
)
STATE_FILE = Path(__file__).parent / "watcher_state.json"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


def fetch_posts():
    resp = requests.get(API_URL, headers={"User-Agent": USER_AGENT}, timeout=30)
    resp.raise_for_status()
    return [{"id": p["id"], "link": p.get("link", "")} for p in resp.json()]


def load_seen():
    if STATE_FILE.exists():
        return set(json.loads(STATE_FILE.read_text()))
    return set()


def save_seen(seen):
    STATE_FILE.write_text(json.dumps(sorted(seen, reverse=True)[:500]))


def post_to_slack(text):
    # unfurl_links/unfurl_media=True so a bare article URL expands into Slack's
    # own rich preview card (image, headline, description) — without it, bot/
    # webhook messages don't unfurl text links by default.
    resp = requests.post(
        WEBHOOK_URL,
        json={"text": text, "unfurl_links": True, "unfurl_media": True},
        timeout=15,
    )
    resp.raise_for_status()


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
        post_to_slack(post["link"])
        seen.add(post["id"])
    save_seen(seen)
    print(f"Posted {len(new_posts)} new article(s).")


if __name__ == "__main__":
    main()
