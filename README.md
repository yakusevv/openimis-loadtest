# openimis-loadtest

Load tests for openIMIS, written with [Locust](https://locust.io).

They exercise the health-financing read paths of the GraphQL API with many simulated users at
once, and fail when too many requests fail or when responses get too slow.

## What is tested

| Scenario | Requests | Share |
|---|---|---|
| claims of a health facility, 10, 20 or 50 per page | `claims` | 5 |
| eligibility check of an insuree | `insurees`, `policiesByInsuree`, `premiumsByPolicies` | 4 |
| claim detail | `claim` | 3 |
| current user | `GET /api/core/users/current_user/` | 1 |
| product list | `products` | 1 |

Each simulated user logs in once, then picks a scenario at random, weighted by its share, every
1 to 3 seconds. The claims, insurees and health facilities it asks for are read from the target
when the run starts, so the tests work against any openIMIS instance that has claims.

## Running in GitHub Actions

The **Load test** workflow runs every night at 03:00 UTC. It tests two lines side by side, as two
jobs of one run: `develop`, and the newest release branch. To run it yourself, open the Actions
tab, choose **Load test** and **Run workflow**.

Each run starts a complete openIMIS stack from
[openimis-dist_dkr](https://github.com/openimis/openimis-dist_dkr) with the demo dataset, adds
synthetic data with the claim module's `seed_synthetic_health_data` command, checks every scenario
with a single user, and then runs the load. A run takes about 20 minutes.

| Input | Default | Meaning |
|---|---|---|
| `release` | `develop` | openIMIS release to test: `develop`, a release such as `26.10`, or `release-branch` for the newest release branch. Several, separated by commas, run as parallel jobs; `develop,release-branch` repeats the nightly run |
| `preset` | `medium` | size of the synthetic data: `small`, `medium` or `large` |
| `users` | `20` | simulated users at the same time |
| `spawn_rate` | `2` | users started per second |
| `run_time` | `5m` | how long the load lasts |
| `fail_ratio` | `0.01` | highest share of failed requests that still passes |
| `p95_ceiling_ms` | `4000` | highest 95th-percentile response time, in ms, that still passes |
| `be_tag`, `fe_tag`, `db_tag` | empty | use another backend, frontend or database image tag |
| `dist_ref` | empty | use another branch or tag of openimis-dist_dkr |

`release-branch` means the newest `release/YY.MM` branch of openimis-be_py whose backend and
frontend images are published. It moves to the next release on its own once that branch exists.

The release selects every component: the `openimis-be`, `openimis-fe` and `openimis-pgsql` images
with that tag, and the `release/<release>` branch of openimis-dist_dkr. When the database image or
the branch does not exist for that release, the run uses `develop` for it instead. The backend
image must include `seed_synthetic_health_data`, which the claim module ships from release 26.10.

### Reading the result

The run's summary page shows which images and branch were used, the size of the dataset, and the
response times of every request type. The full results are attached to the run as an artifact: an
HTML report and CSV and JSON files. When a run fails, the artifact also holds the logs of every
container in the stack.

A run fails when:

- more than `fail_ratio` of the requests fail, counting HTTP errors and GraphQL errors;
- the 95th-percentile response time over all requests exceeds `p95_ceiling_ms`;
- a scenario fails at all in the single-user check;
- the stack does not start, or the data cannot be seeded.

The runner is a shared GitHub-hosted machine that also runs the load generator, so compare runs
with each other rather than reading the numbers as production performance.

## Running locally

Against any openIMIS instance that has claims:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/locust --headless --host https://your-openimis.example.org
```

This runs 20 users for 5 minutes, as set in `locust.conf`. Flags override it, for example
`-u 50 -t 10m`. Without `--headless`, Locust opens a web UI on `http://localhost:8089` where you
start and watch the run.

To keep the results, add `--csv=out/run --html=out/report.html --json-file=out/summary`.

To add synthetic data to an instance first, run this on its backend. Only do this on a database
you can throw away:

```bash
python manage.py seed_synthetic_health_data --preset medium --no-confirm
```

## Configuration

Environment variables read by `locustfile.py`:

| Variable | Default | Meaning |
|---|---|---|
| `LOADTEST_USER`, `LOADTEST_PASSWORD` | `Admin`, `admin123` | account the simulated users log in with; it must see all health facilities |
| `LOADTEST_API_ROOT` | `/api` | path of the openIMIS API |
| `LOADTEST_FAIL_RATIO` | `0.01` | highest share of failed requests that still passes |
| `LOADTEST_P95_MS` | not set | highest 95th-percentile response time, in ms; not checked when not set |
| `LOADTEST_SAMPLE_SIZE` | `200` | how many recent claims are read at the start to draw from |

Locust exits with code 1 when the run fails and 0 when it passes.

The simulated users log in the way the openIMIS web frontend does: the `tokenAuth` mutation, then
the `getCsrfToken` mutation, and then every request carries the session cookies and an
`X-CSRFToken` header. The cookies are sent explicitly, so the tests also work against an instance
served over plain HTTP.
