#!/usr/bin/env python3
"""
Daily Kickoff -> WhatsApp post, delivered to Sruli's Slack DM.

Runs on weekday mornings (see .github/workflows/kickoff.yml). It waits for
today's Daily Kickoff to be published, then builds the WhatsApp post:
the Kickoff headline + the first four stories in it, each with a
link.ji.news short link from Bitly. A story gets an "ANALYSIS | ",
"SCOOP | " or "EXCLUSIVE | " prefix only when its Kickoff tagline is one of
those words. No other labels are ever added.

If something goes wrong, the DM says so instead of staying silent.

Env:
  BITLY_TOKEN              Bitly API token
  KICKOFF_SLACK_WEBHOOK    incoming webhook that posts to Sruli's DM
                           (unset = just print the post)
  JI_BYPASS_TOKEN          lets the GitHub runner past JI's Cloudflare
  KICKOFF_DEADLINE_UTC     hour (UTC) to give up waiting; default 15
"""

import datetime
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

API = "https://jewishinsider.com/wp-json/wp/v2/posts"
BITLY_DOMAIN = "link.ji.news"
NUM_STORIES = 4
LABELS = {"ANALYSIS", "SCOOP", "EXCLUSIVE"}
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
ET = ZoneInfo("America/New_York")
WAIT_SECONDS = 300

BITLY_TOKEN = os.environ.get("BITLY_TOKEN", "").strip()
WEBHOOK = os.environ.get("KICKOFF_SLACK_WEBHOOK", "").strip()
BYPASS = os.environ.get("JI_BYPASS_TOKEN", "").strip()
DEADLINE_HOUR_UTC = int(os.environ.get("KICKOFF_DEADLINE_UTC", "15"))


def get_json(url):
    headers = {"User-Agent": UA}
    if BYPASS:
        headers["X-JI-Watcher-Token"] = BYPASS
    sep = "&" if "?" in url else "?"
    url = f"{url}{sep}_cb={int(time.time())}"  # dodge the 10-min edge cache
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def clean(text):
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    return re.sub(r"\s+", " ", text).strip().replace("'", "’")


def todays_kickoff():
    today = datetime.datetime.now(ET).date().isoformat()
    posts = get_json(f"{API}?search=daily%20kickoff&per_page=10"
                     "&_fields=id,date,link,title,content")
    for p in posts:
        slug = p["link"].rstrip("/").rsplit("/", 1)[-1]
        if slug.startswith("daily-kickoff-") and p["date"].startswith(today):
            return p
    return None


def stories_in(content, kickoff_url):
    """First N story blocks in the Kickoff: (url, tagline)."""
    out, seen = [], set()
    for block in re.split(r"<section ", content)[1:]:
        if "dk-news" not in block[:200]:
            continue
        u = re.search(r'class="headline" data-url="([^"]+)"', block)
        if not u:
            continue
        url = u.group(1).split("?")[0].rstrip("/") + "/"
        if "daily-kickoff" in url or url == kickoff_url or url in seen:
            continue
        if not re.match(r"https?://(www\.)?jewishinsider\.com/\d{4}/\d{2}/", url):
            continue
        tg = re.search(r'class="tagline">\s*<h4>(.*?)</h4>', block, re.S)
        seen.add(url)
        out.append((url, clean(tg.group(1)) if tg else ""))
        if len(out) == NUM_STORIES:
            break
    return out


def article_title(url):
    slug = url.rstrip("/").rsplit("/", 1)[-1]
    posts = get_json(f"{API}?slug={urllib.parse.quote(slug)}&_fields=title")
    if not posts:
        raise RuntimeError(f"couldn't find the article {url}")
    return clean(posts[0]["title"]["rendered"])


def shorten(long_url):
    req = urllib.request.Request(
        "https://api-ssl.bitly.com/v4/shorten",
        data=json.dumps({"long_url": long_url, "domain": BITLY_DOMAIN}).encode(),
        headers={"Authorization": f"Bearer {BITLY_TOKEN}",
                 "Content-Type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)["link"]
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Bitly said {e.code} for {long_url}: {e.read().decode()[:200]}")


def slack(text):
    if not WEBHOOK:
        print(text)
        return
    req = urllib.request.Request(
        WEBHOOK,
        data=json.dumps({"text": text, "unfurl_links": False,
                         "unfurl_media": False}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    urllib.request.urlopen(req, timeout=30).read()


def build():
    if not BITLY_TOKEN:
        raise RuntimeError("BITLY_TOKEN is missing")

    kickoff = None
    while True:
        try:
            kickoff = todays_kickoff()
        except Exception as e:  # transient site hiccup: keep waiting
            print(f"lookup failed: {e}", file=sys.stderr)
        if kickoff:
            break
        if datetime.datetime.now(datetime.timezone.utc).hour >= DEADLINE_HOUR_UTC:
            raise RuntimeError("today's Daily Kickoff never showed up on the site "
                               "(holiday, or it's very late)")
        print("Kickoff not up yet, waiting...", file=sys.stderr)
        time.sleep(WAIT_SECONDS)

    kickoff_url = kickoff["link"].rstrip("/") + "/"
    headline = clean(kickoff["title"]["rendered"])
    headline = re.sub(r"^Daily Kickoff:\s*", "", headline)

    stories = stories_in(kickoff["content"]["rendered"], kickoff_url)
    if len(stories) < NUM_STORIES:
        raise RuntimeError(f"only found {len(stories)} stories in {kickoff_url}")

    lines = [f"Good morning! Check out today's full Daily Kickoff — 👉 {headline} — "
             f"{shorten(kickoff_url)}", "", "More stories:"]
    for url, tagline in stories:
        title = article_title(url)
        label = tagline.upper()
        if label in LABELS:
            title = f"{label} | {title}"
        lines.append(f"* {title}: {shorten(url)}")
    return "\n".join(lines)


def main():
    try:
        post = build()
    except Exception as e:
        slack(f":warning: Couldn't build today's Kickoff WhatsApp post: {e}\n"
              "Run `todays` on your Mac to do it by hand.")
        raise
    # Normally started by the article watcher before 10am ET. A later start
    # means that trigger failed and only GitHub's backup schedule fired.
    if datetime.datetime.now(ET).hour >= 10:
        post = (":warning: Late: the morning trigger didn't fire, so this came from "
                "the backup schedule. Check the article watcher's cron-job.org pinger.\n\n"
                + post)
    slack(post)


if __name__ == "__main__":
    main()
