# openimis-loadtest

Load tests for openIMIS, written for [Locust](https://locust.io). The profile in
`locustfile.py` covers the health-financing read paths:

| Scenario | Requests | Weight |
|---|---|---|
| session | `GET /api/core/users/current_user/` | 1 |
| claims of a health facility, 10 / 20 / 50 per page | `claims` | 5 |
| claim detail | `claim(uuid:)` | 3 |
| eligibility | `insurees(chfId:)`, then `policiesByInsuree`, then `premiumsByPolicies` | 4 |
| products | `products` | 1 |

Each simulated user logs in once, then waits 1–3 s between tasks.

## Running

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/locust --headless --host https://your-openimis.example.org
```

`locust.conf` sets 20 users, spawned at 2 per second, for 5 minutes. Command-line flags
override it, for example `-u 50 -t 10m`. Leave out `--headless` to drive the run from the web UI
on `http://localhost:8089` instead.

Add `--csv=out/run --html=out/report.html --json-file=out/summary` to keep the results. Locust
appends `.json` to the last one.

The target needs data to read. Seed it with the claim module's generator first, on a database you
can throw away:

```bash
python manage.py seed_synthetic_health_data --preset medium --no-confirm
```

At start, the run reads a sample of up to 200 recent claims, with their insurees and health
facilities, from the target. The scenarios draw from that sample, so nothing in this repository
names a record of a particular server. A target without claims stops the run before any user
starts.

## Configuration

Environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `LOADTEST_USER` / `LOADTEST_PASSWORD` | `Admin` / `admin123` | the account every simulated user logs in as; it must see all health facilities |
| `LOADTEST_API_ROOT` | `/api` | the backend's `SITE_ROOT` |
| `LOADTEST_FAIL_RATIO` | `0.01` | fail the run above this share of failed requests |
| `LOADTEST_P95_MS` | unset | fail the run when the aggregate 95th percentile exceeds this many milliseconds |
| `LOADTEST_SAMPLE_SIZE` | `200` | how many claims the start-up sample reads |

The process exits 1 when a threshold is exceeded, when a task raises, or when the start-up sample
cannot be read. Otherwise it exits 0. A request counts as failed on any HTTP status other than 200
and on any GraphQL `errors` in the response.

## Login

The users log in the way the web frontend does. They call the `tokenAuth` mutation, then the
`getCsrfToken` mutation, then send the session and JWT cookies and an `X-CSRFToken` header with
every request. The CSRF header is required: without it, `claims` and `insurees` refuse to answer.
The server marks both cookies `Secure`, so the locustfile attaches them as an explicit `Cookie`
header. That lets the same run target plain HTTP, for example a stack on `http://localhost`.
