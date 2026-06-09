# JI Article Watcher

Posts every newly published Jewish Insider post (articles, features, newsletters,
Daily Kickoff — everything) to a Slack channel, within a few minutes of publish.

Runs 24/7 as a scheduled GitHub Actions workflow — it does **not** depend on any
personal machine being on. It reads the public Jewish Insider WordPress REST API,
so no login or cookie is needed.

## How it works

- `.github/workflows/poll.yml` runs `poll.py` on a `*/5 * * * *` cron (GitHub's
  scheduler is best-effort, so expect ~5–15 min in practice).
- `poll.py` fetches the 20 most recent posts and Slacks any it hasn't seen.
- Seen post IDs are stored in `watcher_state.json`, which the workflow commits
  back to the repo after each run so the next run remembers what it sent.
- The Slack webhook is stored as the `SLACK_WEBHOOK_URL` repository secret, not
  in the code.

## First run

On the very first run (no `watcher_state.json` yet) it records the current posts
as a baseline and posts a single "watcher is live" message — it does not backfill.

## Manual trigger

Actions tab → "JI article watcher" → "Run workflow".

## Local test

```bash
pip install -r requirements.txt
SLACK_WEBHOOK_URL="https://hooks.slack.com/services/..." python poll.py
```
