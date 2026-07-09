#!/usr/bin/env python3
"""
Single-shot poller: checks the Jewish Insider WordPress API for newly published
posts and sends each new article's URL to Slack (Slack unfurls it). Runs once
per invocation from the GitHub Actions pinger.

Sends the X-JI-Watcher-Token header (from the JI_BYPASS_TOKEN repo secret) so
JI's Cloudflare rule allowlists this watcher past its bot block.

Double-post safety (runs can overlap when GitHub is slow):
- Right before posting each article, re-read the freshest "already posted" list
  straight from the repo (origin/main) and skip anything a concurrent run has
  already handled.
- Commit + push each post immediately (merging with origin, retrying on a race)
  so the other run sees it right away.

Reads SLACK_WEBHOOK_URL from env (a repo secret). No third-party deps (stdlib
only) so runs are fast, which also minimizes overlap.
"""

import json
import os
import subprocess
from pathlib import Path
from urllib.request import Request, urlopen

WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")
BYPASS_TOKEN = os.environ.get("JI_BYPASS_TOKEN", "")
API_URL = (
    "https://jewishinsider.com/wp-json/wp/v2/posts"
    "?per_page=20&orderby=date&order=desc&_fields=id,link"
)
STATE_FILE = Path(__file__).parent / "watcher_state.json"
REPO = STATE_FILE.parent
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
GIT_ID = [
    "-c", "user.name=github-actions[bot]",
    "-c", "user.email=github-actions[bot]@users.noreply.github.com",
]


def _get_json(url):
    headers = {"User-Agent": UA}
    if BYPASS_TOKEN:
        headers["X-JI-Watcher-Token"] = BYPASS_TOKEN  # Cloudflare allowlist
    with urlopen(Request(url, headers=headers), timeout=30) as r:
        return json.loads(r.read().decode())


def fetch_posts():
    return [{"id": p["id"], "link": p.get("link", "")} for p in _get_json(API_URL)]


def _parse(text):
    try:
        return set(json.loads(text))
    except Exception:
        return set()


def load_seen():
    return _parse(STATE_FILE.read_text()) if STATE_FILE.exists() else set()


def save_seen(seen):
    STATE_FILE.write_text(json.dumps(sorted(seen, reverse=True)[:500]))


def git(*args):
    return subprocess.run(
        ["git", "-C", str(REPO), *args], capture_output=True, text=True, timeout=30
    )


def latest_seen():
    """Freshest committed state from origin — so we don't repost what another run just did."""
    git("fetch", "-q", "origin", "main")
    r = git("show", "origin/main:watcher_state.json")
    return _parse(r.stdout) if r.returncode == 0 else load_seen()


def record(seen):
    """Push the state as the union of ours + origin's; retry on a concurrent-push race."""
    for _ in range(5):
        git("fetch", "-q", "origin", "main")
        remote = git("show", "origin/main:watcher_state.json")
        merged = seen | (_parse(remote.stdout) if remote.returncode == 0 else set())
        git("reset", "-q", "--hard", "origin/main")
        save_seen(merged)
        git("add", "watcher_state.json")
        if git("diff", "--cached", "--quiet").returncode == 0:
            return  # origin already has everything we do
        git(*GIT_ID, "commit", "-q", "-m", "Update watcher state [skip ci]")
        if git("push", "-q", "origin", "HEAD:main").returncode == 0:
            return
        # push lost the race — loop, re-merge against the new origin, try again


def post_to_slack(text):
    body = json.dumps(
        {"text": text, "unfurl_links": True, "unfurl_media": True}
    ).encode()
    urlopen(
        Request(WEBHOOK_URL, data=body, headers={"Content-Type": "application/json"}),
        timeout=15,
    ).read()


def main():
    if not WEBHOOK_URL:
        print("SLACK_WEBHOOK_URL not set yet — nothing to do.")
        return

    posts = fetch_posts()

    if not STATE_FILE.exists():
        # First ever run — baseline silently, say hello once.
        seen = {p["id"] for p in posts}
        save_seen(seen)
        post_to_slack(
            ":eyes: Article watcher is live. New Jewish Insider posts will appear "
            "here as they publish."
        )
        record(seen)
        print(f"First run — baseline of {len(posts)} posts saved.")
        return

    new_posts = [p for p in posts if p["id"] not in latest_seen()]
    if not new_posts:
        print(f"No new posts ({len(posts)} on feed).")
        return

    posted = 0
    for post in reversed(new_posts):  # oldest first
        seen = latest_seen()  # re-check the freshest list right before posting
        if post["id"] in seen:
            continue  # a concurrent run already posted it
        post_to_slack(post["link"])
        seen.add(post["id"])
        save_seen(seen)
        record(seen)  # record immediately so the other run sees it
        posted += 1
    print(f"Posted {posted} new article(s).")


if __name__ == "__main__":
    main()
