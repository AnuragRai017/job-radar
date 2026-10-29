# RoleRadar

A fault-tolerant job aggregator for fourteen public career sites — including
**Deloitte**, **Capgemini**, **JPMorgan Chase**, **Google**, **Amazon**,
**Microsoft**, **HSBC**, **EY**, **EXL**, **Concentrix**, **Cadence**, and
**Medallion** — with a searchable web UI, a read-only JSON API, and a scheduled
scraper.

RoleRadar is deliberately small and self-hosted: a FastAPI backend, PostgreSQL,
an APScheduler worker, and a static Astro frontend served by Nginx. Everything
runs in Docker Compose and binds to loopback only.

```
Browser ──:5100──► Nginx (Astro build) ──/api,/health──► FastAPI ──► PostgreSQL
                                                             ▲
                                                             │ upserts
                                                   APScheduler worker
                                                             │
                                        public career sites (robots-aware)
```

## What it does

- **Searches fourteen public feeds in one place.** Keyword, company, and location
  filters with pagination. Full descriptions live on `/job/?id=…` and every
  apply button points at the company’s own careers site.
- **Stores jobs idempotently.** A unique `(source, external_id)` constraint plus
  an upsert means repeated sweeps never duplicate rows.
- **Scrapes politely.** 1.5-second per-host rate limit, User-Agent rotation,
  exponential backoff with jitter, and `robots.txt` enforcement.
- **Survives failures.** Each source is isolated; one broken site cannot kill a
  run. Partial runs keep existing jobs and only complete runs deactivate roles
  that disappeared.
- **Refreshes every six hours.** APScheduler runs the sweep in a dedicated
  worker with `max_instances=1`, coalescing, and graceful `SIGTERM` shutdown.
- **Skips unchanged detail pages.** Known jobs with a description and unchanged
  title/date do not trigger another detail request.
- **Optional AI rescue.** If a Deloitte page changes enough that the Avature
  parser cannot find listings or JSON-LD, an Ollama model can extract the missing
  data. It is disabled by default and never replaces the deterministic parsers.

### Sources

| Source | Method | Notes |
| --- | --- | --- |
| Google | `AF_initDataCallback` `ds:1` JSON blob on the public results page | robots.txt disallows `?page=`; strict mode fetches only the first page (20 jobs) |
| Deloitte | Avature search HTML for IDs, then schema.org `JobPosting` JSON-LD | Description, title, and date come from structured data; optional Ollama fallback if the markup changes |
| Capgemini | Public WordPress JobStream JSON API (`/wp-json/llm/v1/jobs`) | robots.txt explicitly allows the path |
| JPMorgan Chase | Oracle Recruiting Cloud public REST API | robots.txt returns 403; treated as “allow” per RFC 9309 §2.3.1.3 |
| Deloitte India | SuccessFactors search HTML + `.jobdescription` detail | Uses the India search behind the supplied campaign URL; 25 results/page; filled requisitions are skipped |
| EY | SuccessFactors search HTML + `.job` detail | India filter (`locationsearch=India`); 25 results/page |
| Capgemini India | Same public JobStream JSON API with `country_code=in-en` | 788 India roles; overlaps with the global Capgemini feed |
| EXL | Oracle Recruiting Cloud REST API (`CX_2`) | 2,100 India requisitions via the `locationId` facet; same API shape as JPMorgan |
| Concentrix | Embedded `jobsData` JSON on the India search page | 73 roles; one request; robots.txt asks for a 10-second crawl delay |
| Amazon | Public `search.json` with `country=IND` | 2,100+ roles; full descriptions in the list; 100 per page |
| Microsoft | Eightfold PCSX JSON API (`/api/pcsx/search` + `position_details`) | CSRF token from the public careers page; 10 per page |
| Medallion | Public Greenhouse board API (`medallionakafirstlayerai`) | 6 roles; full content in the list response |
| Cadence | Public open-roles HTML + detail pages | 21 roles; description in `.job-description__content` |
| HSBC | Eightfold `/api/apply/v2/jobs` API | 220 India roles; detail endpoint supplies descriptions |

Counts change daily. A full live pass in October 2026 saw roughly 7,300 JPMC,
6,800 Capgemini, 1,500 Deloitte, and 3,400 Google postings globally (strict
robots stores only the first 20 Google results). The India-focused
feeds add roughly 3,100 EY, 2,100 EXL, 2,100 Amazon, 790 Capgemini India, 240
Microsoft, 220 HSBC, 73 Concentrix, 21 Cadence, and 6 Medallion roles, plus the
paginated Deloitte India search.

## Quick start (Docker)

Requirements: Docker Engine/Desktop with Compose v2, and a network connection.
The backend image runs Python 3.13; the frontend build stage uses Node 24.

```bash
docker compose up -d --build
```

Then open:

- **Web UI** — <http://127.0.0.1:5100>
- **API docs** — <http://127.0.0.1:5200/api/docs>
- **Health** — <http://127.0.0.1:5200/health>

No `.env` file is required; Compose starts with safe local defaults. To tune
scraping or change the database password, copy the example file first:

```bash
cp .env.example .env        # Windows PowerShell: Copy-Item .env.example .env
```

Compose always uses PostgreSQL. A `DATABASE_URL` in `.env` is only read by the
non-Docker local workflow.

### First sweep

`SCRAPE_ON_STARTUP=true` starts a sweep when the scheduler container comes up.
A **full** pass is intentionally slow: tens of thousands of listings and many
thousands of detail pages at 1.5 seconds per host can take many hours. The UI
fills in source by source as each scraper finishes; the fast India/new sources
run first in the default order, followed by the large existing feeds.

For a quick demo, cap each source in `.env` and recreate the scheduler:

```dotenv
SCRAPE_MAX_JOBS_PER_SOURCE=25
```

```bash
docker compose up -d --force-recreate scheduler
```

Set it back to `0` (unlimited) for the full feed. Google only contributes 20
jobs under strict robots rules regardless of the cap.

### Useful commands

```bash
docker compose ps
docker compose logs -f scheduler
docker compose exec scheduler python -m app.cli stats
docker compose exec scheduler python -m app.cli scrape --sources google --limit 20
docker compose exec scheduler python -m app.cli scrape --sources amazon,microsoft --limit 5
docker compose down            # keeps the pgdata volume
docker compose down -v         # also deletes the database
```

### Production overrides

`docker-compose.prod.yml` adds resource ceilings and bounded container logs:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

Before using this anywhere beyond your own machine:

1. Change `POSTGRES_PASSWORD` in `.env`.
2. Keep the published ports on `127.0.0.1` and put a TLS-terminating reverse
   proxy (with authentication) in front if remote access is needed.
3. Never publish the database port.

## API

Interactive docs: `/api/docs`. All responses are JSON; times are ISO-8601 UTC.

| Endpoint | Description |
| --- | --- |
| `GET /api/jobs` | Paginated, active jobs. Query: `page`, `page_size` (1–100), `company`, `location`, `keyword`, `include_inactive` |
| `GET /api/jobs/{id}` | Full description and provenance for one job |
| `GET /api/stats` | Counts by company, latest run, last successful sweep |
| `GET /health` | API + database health and last scrape time |

Examples:

```bash
curl "http://127.0.0.1:5200/api/jobs?company=Google&location=London&page_size=10"
curl "http://127.0.0.1:5200/api/jobs/42"
curl "http://127.0.0.1:5200/api/stats"
```

Jobs are ordered by posted date (newest first), then id. Keyword search covers
title, description, location, company, and external id with escaped `LIKE`
patterns. The API is read-only by design; there are no write endpoints.

## Configuration

All settings are environment variables; see `.env.example` for the annotated
list. The ones that matter most:

| Variable | Default | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | SQLite locally, PostgreSQL in Compose | SQLAlchemy connection string |
| `SCRAPE_SOURCES` | all 14 sources (`jpmorgan` … `ey`) | Comma-separated scrapers to run |
| `SCRAPE_INTERVAL_HOURS` | `6` | Scheduler interval |
| `SCRAPE_ON_STARTUP` | `true` | Run immediately when the worker starts |
| `SCRAPE_MAX_JOBS_PER_SOURCE` | `0` | Per-source cap; `0` = unlimited |
| `SCRAPE_MAX_PAGES_PER_SOURCE` | `0` | Page ceiling; `0` = scraper default |
| `HTTP_RATE_LIMIT_SECONDS` | `1.5` | Minimum delay between requests to one host |
| `HTTP_MAX_RETRIES` | `4` | Retries for transient HTTP errors |
| `RESPECT_ROBOTS` | `true` | Enforce robots.txt with `protego` |
| `GOOGLE_PAGINATION_OVERRIDE` | `false` | Opt into Google `?page=` pagination (see compliance) |
| `CORS_ORIGINS` | localhost UI origins | Allowed browser origins |
| `AI_FALLBACK_ENABLED` | `false` | Enable the optional Ollama extraction fallback |
| `OLLAMA_BASE_URL` | `http://host.docker.internal:11434` | Ollama endpoint used by the fallback |
| `OLLAMA_MODEL` | `gpt-oss:120b-cloud` | Installed Ollama model tag |
| `AI_FALLBACK_MAX_CALLS` | `10` | Per-run AI call budget |

The Docker stack derives `DATABASE_URL` from `POSTGRES_DB`, `POSTGRES_USER`, and
`POSTGRES_PASSWORD`, so those three values in `.env` are the ones to change.

## Optional Ollama AI extraction fallback

The deterministic parsers remain the primary path. If a Deloitte search page
returns no job cards, or a detail page has no usable `JobPosting` JSON-LD
description, RoleRadar can ask an Ollama model to extract the missing data. The
fallback is off by default and applies only to Deloitte.

The model response is constrained to JSON, validated with Pydantic, passed
through the same sanitizer as normal descriptions, and listing URLs must point
at an allowed `apply.deloitte.com/.../JobDetail/...` path. AI failures never
break the deterministic path.

### Enable it

1. Make sure Ollama is running and the model exists:

   ```bash
   ollama list
   # Local model, if your Ollama version provides it:
   ollama pull gpt-oss:120b
   ```

   The Ollama instance on this host currently exposes `gpt-oss:120b-cloud`.
   That tag sends page content to `ollama.com`; use a local tag if content must
   stay on this machine.

2. Set these values in `.env`:

   ```dotenv
   AI_FALLBACK_ENABLED=true
   OLLAMA_BASE_URL=http://host.docker.internal:11434
   OLLAMA_MODEL=gpt-oss:120b-cloud
   ```

3. Rebuild and recreate the backend containers so they pick up the code and
   environment:

   ```bash
   docker compose up -d --build --force-recreate api scheduler
   ```

4. Watch the scheduler logs for fallback calls and errors:

   ```bash
   docker compose logs -f scheduler
   ```

Compose adds the `host.docker.internal:host-gateway` mapping for Linux. On a
Linux host, Ollama must listen on an address reachable from the Docker bridge,
for example `OLLAMA_HOST=0.0.0.0:11434`; a loopback-only listener is not
reachable from containers. For local (non-Docker) backend runs, use
`OLLAMA_BASE_URL=http://127.0.0.1:11434`.

### Guardrails

- **Disabled by default, fallback only.** It is only called after a deterministic
  parser yields no listings or no usable detail description. It never replaces
  the source parsers.
- **Budget.** `AI_FALLBACK_MAX_CALLS` (default 10) caps calls per scrape run.
- **Timeout.** `OLLAMA_TIMEOUT_SECONDS` (default 120) caps each request.
- **Circuit breaker.** Three consecutive failures disable the fallback for the
  rest of the run.
- **Input bound.** `AI_FALLBACK_MAX_INPUT_CHARS` (default 60,000) truncates page
  content before it is sent.
- **Output checks.** JSON schema validation, HTML sanitization, and URL
  allowlisting reject malformed or hallucinated data.
- **Privacy.** Every AI request sends page HTML to the configured Ollama
  endpoint. `gpt-oss:120b-cloud` forwards it to `ollama.com`; run a local model
  if that is not acceptable.
- **Compliance.** The fallback does not fetch extra pages, bypass robots.txt, log
  in, or solve CAPTCHAs. It only reads a page the deterministic scraper already
  fetched.

## Local development

### Backend (SQLite)

Requires Python 3.11 or newer; the Docker image uses Python 3.13.

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt

# Optional: seed a few jobs without Docker
python -m app.cli scrape --sources google --limit 20

uvicorn app.main:app --reload --host 127.0.0.1 --port 5200
```

SQLite defaults to `backend/data/jobs.db`, uses WAL mode, and needs no server.
The database and tables are created automatically on first use.

### Frontend (Astro dev server)

```bash
cd frontend
npm install
npm run dev
```

Open <http://127.0.0.1:4321>. Vite proxies `/api` and `/health` to
`http://127.0.0.1:5200`, so the same relative URLs work in development and
behind Nginx. Override the proxy target with `API_PROXY_TARGET`.

For a production build, set `PUBLIC_SITE_URL` (canonical origin) and run:

```bash
PUBLIC_SITE_URL=http://127.0.0.1:5100 npm run build
```

The output in `frontend/dist` is fully static. The container build does this
automatically.

## Tests and linting

```bash
cd backend
python -m pytest
python -m ruff check app tests
```

The test suite uses a temporary SQLite database and covers the sanitizer,
repository upserts/deactivation, API filters/pagination/detail/stats, all
fourteen source parsers, and the AI fallback's JSON, URL, budget, and
sanitization guardrails. It makes no live network requests.

Frontend checks:

```bash
cd frontend
npm run check
npm run build
```

## Project structure

```
.
├── backend
│   ├── app
│   │   ├── api/routes.py         # /api/jobs, /api/jobs/{id}, /api/stats
│   │   ├── scrapers
│   │   │   ├── ai_fallback.py    # optional Ollama JSON extraction fallback
│   │   │   ├── amazon.py         # Amazon Jobs search.json (India)
│   │   │   ├── base.py           # HTTP client, retries, robots, rate limits
│   │   │   ├── cadence.py        # Cadence open-roles HTML
│   │   │   ├── capgemini.py      # WordPress JobStream API (global + India)
│   │   │   ├── concentrix.py     # embedded jobsData JSON
│   │   │   ├── deloitte.py       # US Avature listing + JSON-LD detail
│   │   │   ├── eightfold.py      # HSBC + Microsoft Eightfold APIs
│   │   │   ├── exl.py            # EXL Oracle Recruiting (CX_2)
│   │   │   ├── google.py         # ds:1 blob parser + robots gate
│   │   │   ├── greenhouse.py     # Medallion Greenhouse board API
│   │   │   ├── jpmorgan.py       # JPMorgan Oracle Recruiting
│   │   │   ├── oracle.py         # shared Oracle Recruiting API logic
│   │   │   └── successfactors.py # Deloitte India + EY SuccessFactors
│   │   ├── config.py             # environment settings
│   │   ├── database.py           # engine, sessions, init retries
│   │   ├── models.py             # Job + ScrapeRun
│   │   ├── repository.py         # idempotent upserts, stale handling
│   │   ├── runner.py             # per-source isolation + run records
│   │   ├── scheduler.py          # APScheduler worker, SIGTERM handling
│   │   ├── sanitize.py           # bleach/BeautifulSoup description pipeline
│   │   ├── schemas.py            # Pydantic API contracts
│   │   ├── cli.py                # manual scrape / stats commands
│   │   └── main.py               # FastAPI app, CORS, /health
│   ├── tests
│   ├── Dockerfile
│   ├── pyproject.toml
│   └── requirements*.txt
├── frontend
│   ├── src
│   │   ├── layouts/BaseLayout.astro
│   │   ├── pages/index.astro     # search, filters, pagination
│   │   ├── pages/job.astro       # /job/?id=… detail view
│   │   ├── scripts/api.ts        # typed fetch helpers + formatting
│   │   └── styles/global.css     # light/dark design system
│   ├── nginx.conf
│   ├── Dockerfile
│   └── package.json
├── docker-compose.yml
├── docker-compose.prod.yml
└── .env.example
```

The detail view is a static page that reads `?id=` at runtime. That keeps the
frontend build independent of database contents and avoids rebuilding the image
on every scrape.

## Data and failure behaviour

- **Upserts.** `POST`-less, idempotent writes keyed on source + external id.
  Descriptions are sanitized with an allowlist (`bleach`) and active elements
  such as `<script>` are removed along with their contents.
- **Incremental detail fetches.** If a stored job has a description and its
  listing title (and, where reliable, location/date) is unchanged, the detail
  page is not requested again.
- **Stale jobs.** A job is marked inactive only when a source run is complete
  and did not see it. Partial or failed runs never deactivate anything.
- **Closed requisitions.** SuccessFactors search pages still show some filled
  jobs. Their detail pages say “position has been filled”, so RoleRadar skips
  them and removes them from the seen set instead of storing an error message.
- **Run records.** Every pass writes a `scrape_runs` row with per-source status,
  counts, notes, duration, and the overall status (`success`, `partial`,
  `failed`). `/api/stats` exposes the latest.
- **Retries.** 403/408/425/429/5xx responses and network errors are retried
  with exponential backoff, jitter, and rotating User-Agents.
- **Robots.** `protego` handles RFC 9309 group merging correctly (avoiding a
  Python 3.13 `urllib.robotparser` bug). A 4xx robots response is treated as
  “allow”, as RFC 9309 specifies. A 5xx response or an unreachable robots file
  is treated as “deny” and retried after five minutes; the affected source is
  skipped for that run while existing jobs are kept. Disallow rules are enforced
  before every request.

## Compliance notes

RoleRadar only reads publicly accessible pages. It does not log in, bypass
CAPTCHAs, or use stolen credentials. That said, **you are responsible for how
you run it**:

- **Google.** `robots.txt` disallows the `?page=` results parameter. With the
  default `GOOGLE_PAGINATION_OVERRIDE=false`, the scraper fetches only the
  allowed first page (20 jobs) and records a note explaining the shortfall.
  Setting the override to `true` is an explicit, logged decision to ignore that
  rule for this source. Review Google’s terms and robots.txt before doing so;
  the default configuration is the conservative one.
- **JPMorgan Chase.** The robots endpoint returns HTTP 403. Per RFC 9309, a 4xx
  response is treated as “allow”, so the scraper proceeds. Re-check this if the
  site changes.
- **Deloitte / Capgemini.** Discovery and detail pages are public. Capgemini’s
  JSON endpoint is explicitly allowed by robots.txt. Keep the rate limit at
  1.5 seconds or higher; do not parallelize requests to the same host.
- **Deloitte India / EY.** SuccessFactors search and `/job/` detail pages are
  public. robots.txt disallows `/services/` and `/applybutton/`; RoleRadar does
  not call those paths. Search cards for filled requisitions are skipped.
- **Eightfold (Microsoft, HSBC).** robots.txt explicitly allows `/careers`,
  `/api/apply`, and `/api/pcsx`; the scrapers use only those public endpoints.
- **Amazon / Medallion / Cadence / Concentrix.** Amazon’s `search.json` is
  allowed (only `/internal` is disallowed). Medallion’s Greenhouse board API is
  public and its robots file allows crawlers. Cadence allows job pages (the
  disallowed `search-index.json` is not used). Concentrix allows the search page
  but asks for a 10-second crawl delay; the scraper makes a single request.
- **EXL / Capgemini India.** EXL’s Oracle site returns 404 for robots.txt, which
  RFC 9309 treats as “allow”; Capgemini India reuses the explicitly allowed
  JobStream API with `country_code=in-en`.
- **Attribution.** Every listing links to the original posting. RoleRadar is not
  affiliated with any of the companies, and company names/trademarks belong to
  their owners.
- **Ollama fallback.** When `AI_FALLBACK_ENABLED=true`, page HTML is sent to the
  configured Ollama server. The default `gpt-oss:120b-cloud` tag forwards that
  content to `ollama.com`. Review that provider's terms and use a local model if
  the content must stay on your machine. Enabling the fallback does not change
  robots.txt behaviour or add extra page fetches.
- **Personal use.** A public deployment that republishes full descriptions may
  raise copyright or terms-of-service questions. Review before exposing it.

## Troubleshooting

| Symptom | Likely cause / fix |
| --- | --- |
| UI shows “The radar went quiet” | API not healthy; `docker compose ps`, `docker compose logs api` |
| UI is empty but scheduler is running | First full sweep still in progress, or cap is low. `docker compose exec scheduler python -m app.cli stats` |
| Only 20 Google jobs | Expected under strict robots; see compliance notes |
| `database unavailable` in API logs | PostgreSQL still starting; the API retries table creation for ~24s. Check `docker compose logs db` |
| Port 5100/5200 already in use | Change `FRONTEND_HOST_PORT` / `API_HOST_PORT` in `.env` and re-run Compose |
| Scraper returns 403/429 | The site may be throttling or blocking. Raise `HTTP_RATE_LIMIT_SECONDS`; do not disable robots casually |
| India source has fewer jobs than the site | Filled/closing requisitions still appear in SuccessFactors search but are skipped when the detail page says the position is filled |
| First full sweep takes hours | Expected across 14 sources; run a capped smoke test with `python -m app.cli scrape --sources amazon,microsoft --limit 5` |
| AI fallback never runs | Set `AI_FALLBACK_ENABLED=true` and recreate the scheduler. `docker compose exec scheduler python -c "import urllib.request; print(urllib.request.urlopen('http://host.docker.internal:11434/api/tags', timeout=5).status)"` should print `200` |
| Ollama model not found | Run `ollama list`; set `OLLAMA_MODEL` to an installed tag. Cloud tags such as `gpt-oss:120b-cloud` require a signed-in Ollama account |
| Scheduler misses a run | Runs are coalesced with a 1-hour misfire grace; a full sweep can exceed 6 hours, so the next run starts after it finishes |

Logs are line-oriented and timestamped in UTC. The scheduler logs each source’s
found/upserted/error counts and any robots or pagination decisions.
