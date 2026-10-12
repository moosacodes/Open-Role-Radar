# OpenRoleRadar

First-party early-career roles (internships, co-ops, new grad) scraped from public employer ATS boards and published as a searchable static site.

[![CI](https://github.com/moosacodes/Open-Role-Radar/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/moosacodes/Open-Role-Radar/actions/workflows/ci.yml)
[![Sync](https://github.com/moosacodes/Open-Role-Radar/actions/workflows/sync-fast.yml/badge.svg?branch=main)](https://github.com/moosacodes/Open-Role-Radar/actions/workflows/sync-fast.yml)

**Live:** https://moosacodes.github.io/Open-Role-Radar/

## What it does

- Polls **public** Greenhouse, Lever, Ashby, Workday, Workable, and SmartRecruiters boards on a GitHub Actions schedule
- **Fast path every 5 minutes**: 4 parallel shards poll Greenhouse / Lever / Ashby / Workable / SmartRecruiters (same public ATS JSON APIs Simplify scrapes), plus their GitHub listings dump that auto-promotes new boards into our direct poll list — then always merges and publishes `live-feed`
- Classifies early-career titles (intern / co-op / new-grad / junior / engineer I / campus, plus ATS metadata tags)
- Exports static JSON + Atom feeds (`jobs` strict CS + `jobs-wide`); browser defaults to **new since last visit** / last 24h; `delta.json` lists roles opened/closed since the last sync
- Optional [personal watch template](templates/user-watch/) alerts on **new** matches via GitHub Issues, Discord, or Telegram using your own Actions minutes

## What it does not do

- Scrape LinkedIn or Indeed (aggregator noise; we only use first-party apply URLs)
- Depend on SWE List as a data source — SWE List itself just polls SimplifyJobs GitHub JSON; we hit the underlying ATS boards directly and refresh more often than their ~30m dump
- Cover every employer on earth — boards still need a public API we can poll
- Guarantee sub-minute latency — GitHub Actions schedules bottom out around 5 minutes

## Repo layout

| Path | Role |
| --- | --- |
| `engine/` | Python sync, classify, export |
| `site/` | Astro search UI |
| `config/` | Seed companies, taxonomy |
| `docs/` | Architecture and ops |

## Local setup

Needs Python 3.12+, [uv](https://docs.astral.sh/uv/), Node 22+, pnpm.

```powershell
./scripts/bootstrap.ps1
```

```bash
./scripts/bootstrap.sh
cd engine && uv run openroleradar sync --sample
cd ../site && pnpm dev
```

Optional: copy `.env.example` → `.env`.

## CLI

| Command | Description |
| --- | --- |
| `openroleradar sync` | Poll sources and update state |
| `openroleradar build-data` | Export API, feeds, site shards |
| `openroleradar discover` | Find, validate, and promote new ATS boards |
| `openroleradar validate` | Validate seeds / summarize state |

## Add a company

Edit `config/sources.yml`:

```yaml
- company: Acme Corp
  domain: acme.com
  adapter: greenhouse
  tenant: acme
```

Then open a PR. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Docs

- [ARCHITECTURE.md](docs/ARCHITECTURE.md)
- [API.md](docs/API.md)
- [OPERATIONS.md](docs/OPERATIONS.md)

## License

MIT — see [LICENSE](LICENSE). Published data: [DATA_LICENSE.md](DATA_LICENSE.md).

OpenRoleRadar is not affiliated with any employer or ATS. Verify details on the official careers page before applying.

