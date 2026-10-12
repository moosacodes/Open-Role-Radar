# OpenRoleRadar Personal Watch

Run your own lightweight **new-role alerts** on free GitHub Actions minutes. Alerts only fire for roles you have not seen before.

## Quick start

1. Copy `workflow.yml` into **your** repo at `.github/workflows/openroleradar-watch.yml`.
2. Create label `openroleradar-watch` (or edit the workflow).
3. Optional variables (**Settings → Secrets and variables → Actions → Variables**):

| Variable | Default | Purpose |
| --- | --- | --- |
| `ORR_FEED_URL` | Pages `jobs.atom` | Use `jobs-wide.atom` for all disciplines |
| `ORR_KEYWORDS` | `intern,new grad,graduate,co-op,coop` | Title/summary match |
| `ORR_LOCATIONS` | _(empty)_ | Location hints |
| `ORR_COMPANIES` | _(empty)_ | Company name substrings |
| `ORR_ONLY_NEW` | `true` | Skip roles already alerted |

4. Optional secrets for instant pings:

| Secret | Purpose |
| --- | --- |
| `DISCORD_WEBHOOK` | Discord channel webhook URL |
| `TELEGRAM_BOT_TOKEN` | Bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | Your chat / channel id |

5. Default schedule is every **15 minutes** — edit the cron if you want.

## How it works

1. Downloads the public Atom feed.
2. Diffs entry ids against a cached `seen_ids.json` (Actions cache).
3. Opens a GitHub issue and/or pings Discord/Telegram **only for new ids**.

No OpenRoleRadar API keys. Forks of this template repo do not run against OpenRoleRadar’s minutes — they use **yours**.

## Tips

- First run may alert on the whole current feed; after that only newcomers.
- Set `ORR_ONLY_NEW=false` once if you want a full dump.
- Pair with the site’s **Since last visit** filter while browsing manually.
