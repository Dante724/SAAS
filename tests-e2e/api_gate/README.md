# API gate: the final-gate harness

Live-API checks that every QA finding stays fixed, that shops can't see each other, that new
shops start right in each launch country, and that the core endpoints hold up under load.
Python 3.9+ standard library only. Nothing to install.

They talk to a running server (default `http://localhost:8090`, the Postgres 16 retest server).
Point them elsewhere with `TEKLY_URL`:

```
TEKLY_URL=http://localhost:8090 python3 tests-e2e/api_gate/regress.py
```

Every run signs up its own test shops (`gate-…@tekly.test`). Emails and passwords are generated
at runtime and only kept in memory: nothing is written to disk and no credential is printed.
Run against a dev or staging server, never production: the scripts create real records.

## The scripts

| Script | What it does | Sign-ups | Takes |
|---|---|---|---|
| `regress.py` | One check per Critical/High/Medium QA finding (81 checks) | 3 | ~4 min |
| `isolation.py` | ISO-1: shop B calls every `{id}` route with shop A's real ids | 2 | ~2 min |
| `countries.py` | New shop in AU, US, GB, CA, CA-ON, CA-AB, CA-ZZ, DE, NZ: currency, time zone, tax on a job line | 9 | ~1 min, longer when rate-limited |
| `load.py` | 15 users (5 shops x owner + 2 staff) for 60 s on the core endpoints | 5 | ~2 min |

`gate.py` is the shared library (HTTP client, throttle, sign-up, staff, barrier races, check runner).

### regress.py

```
python3 tests-e2e/api_gate/regress.py              # all checks
python3 tests-e2e/api_gate/regress.py QA-1 W3-C-2  # just these
```

Check ids match the QA docs:

- `QA-1` … `QA-15`: the Critical/High table in `QA_multi-shop_2026-10-31.md`
- `A-1` … `F-6`: the High/Medium bullets of sections A–F in `QA_fix-plan_2026-10-31.md`, in order
- `W3-A-1` … `W3-E-7`: the High then Medium items of each section in `QA_fix-plan_wave3.md`, in order

Each check prints `PASS`, `FAIL` or `SKIP` with a one-line reason (the request and response
when something is wrong), then a summary line. **Pass = 0 FAIL** (exit code 0).

Concurrency checks release three requests at once with a `threading.Barrier` (two for the
"two staff on one line" and double-tap cases) and then check that stock and money moved exactly
once: estimate accept + convert, recommendations decide, pay/prepay with one Idempotency-Key,
transfer ready/completed, stocktake post, line edit/delete, invoice issue, till return,
PO receive, duplicate SKU, coupon, check-in convert.

**Pending waves.** W3-C (returns/refunds/archive) and W3-E (jobs/roles/auth/receipts) may not be
merged yet. A probe decides from behaviour, not branch names:

- W3-C is pending while `POST /api/returns` accepts a return of a part the job never used.
- W3-E is pending while a role made from the Specialist template has `can_edit_closed_ticket`.

While a wave is pending, its failing checks print `SKIP pending W3-C (…what failed…)` instead of
`FAIL`. Once the probe flips, the same checks FAIL for real. (`W3-B-7` needs W3-C and is gated the same way.)

Note: `/api/reports/dashboard` is cached for 30 s per server process, so the revenue checks
(C-1, C-2, C-4, W3-B-7) wait 31 s before each read.

### isolation.py

Shop A gets one record of ~50 kinds (job, line, estimate, client, part, PO, transfer, stocktake,
sale, invoice, role, employee, check-in, templates, …). For every route in `/openapi.json` with
an `{id}` in its path, shop B's owner calls it with A's real id: GET first, then POST/PUT/PATCH
(with a minimal body built from the schema), then DELETE. Public token pages, provider webhooks
and platform-admin routes are left out (they aren't scoped by a signed-in shop user).
Routes whose id kind we can't seed are skipped and counted.

Before and after the sweep every seeded A record is read back as A and compared.

**Pass = 0 leaks, 0 2xx, 0 5xx** (exit code 0). The summary line gives the number of probes and leaks:

- *leak*: a 2xx whose body holds A's data, A's data changed, or a 2xx write removed A's record
- *2xx-without-A-data*: B got a 200 (e.g. an empty list) instead of 404 for A's id; no data
  crossed, but it isn't the 404 we want
- *refused-before-lookup*: 400/405/409/422 on the body before the id was looked up (listed, not a leak)

### countries.py

Signs up one shop per country and checks the company currency code and time zone, the default
tax (name and rate), and that a new job's service line carries that rate and mode
(`included` for AU/GB/DE/NZ, `imposed` for US/CA).

Canada's tax follows the province: CA-ON gets HST 13%, CA-AB GST 5%. A CA sign-up with no
province, or one sign-up doesn't know (CA-ZZ, which must still sign up), takes the province from
its time zone; the default `America/Toronto` is Ontario, so HST 13%. While sign-up has no province
at all (W3-E pending) the province rows SKIP. **Pass = 0 FAIL.**

```
python3 tests-e2e/api_gate/countries.py              # all rows
python3 tests-e2e/api_gate/countries.py CA-AB CA-ZZ  # just these
```

### load.py

Don't run it while something else is stressing the same machine; the numbers mean nothing then.

Each user loops for `LOAD_SECONDS` (default 60): think 0.2–0.6 s, then one action for their role
(board, job list/open/create, add line, pay, clients, parts, dashboard). It prints per-endpoint
p50/p95/max, the status codes, the 5xx count and requests per second.
**Pass = 0 x 5xx and every endpoint's p95 under 1 s.**

## Limits the scripts respect

- **Sign-up is rate-limited** (10 per hour per IP per server process). The scripts reuse a few
  shops per run and, on a 429, wait out `Retry-After` (up to `GATE_MAX_SIGNUP_WAIT`, default
  1800 s). Running all four scripts back to back needs 19 sign-ups, so expect waits.
- **Under 300 requests a minute.** A client-side throttle holds every script to
  `GATE_MAX_RPM` (default 280). `load.py` lifts it for the timed phase only.
- The server's own per-user limit is 600 a minute.

## Leftover data

Test shops are named `Gate …` with `@tekly.test` owners. They aren't deleted afterwards.
