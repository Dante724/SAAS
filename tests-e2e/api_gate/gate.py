"""Shared helpers for the API gate scripts: HTTP client, throttle, test orgs, check runner.

Stdlib only. Every org, email and password is generated here at runtime and kept in memory;
nothing is written to disk and no credential is ever printed.
"""
import json
import os
import secrets
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from collections import deque

BASE_URL = os.environ.get("TEKLY_URL", "http://localhost:8090").rstrip("/")

# Stay under 300 requests a minute (load.py turns this off).
MAX_PER_MINUTE = int(os.environ.get("GATE_MAX_RPM", "280"))
# How long to wait out a 429 on sign-up before giving up.
MAX_SIGNUP_WAIT_S = int(os.environ.get("GATE_MAX_SIGNUP_WAIT", "1800"))


# ---------------------------------------------------------------- throttle

class Throttle:
    """At most `per_minute` requests in any 60 s window, across all threads."""

    def __init__(self, per_minute):
        self.per_minute = per_minute
        self.sent = deque()
        self.lock = threading.Lock()

    def wait(self):
        if not self.per_minute:
            return
        while True:
            with self.lock:
                now = time.monotonic()
                while self.sent and self.sent[0] < now - 60:
                    self.sent.popleft()
                if len(self.sent) < self.per_minute:
                    self.sent.append(now)
                    return
                sleep_for = self.sent[0] + 60 - now
            time.sleep(max(0.05, sleep_for))


THROTTLE = Throttle(MAX_PER_MINUTE)
REQUEST_COUNT = [0]


# ---------------------------------------------------------------- HTTP

class Resp:
    def __init__(self, method, url, status, text, headers, elapsed):
        self.method = method
        self.url = url
        self.status = status
        self.text = text
        self.headers = headers
        self.elapsed = elapsed
        try:
            self.body = json.loads(text) if text else None
        except ValueError:
            self.body = None

    @property
    def ok(self):
        return 200 <= self.status < 300

    def get(self, key, default=None):
        return self.body.get(key, default) if isinstance(self.body, dict) else default

    def short(self, n=220):
        """One-line evidence: METHOD path -> status body…"""
        path = self.url.replace(BASE_URL, "")
        return f"{self.method} {path} -> {self.status} {self.text[:n]!r}"

    def __repr__(self):
        return self.short()


class Api:
    """One signed-in person (or anonymous when token is None)."""

    def __init__(self, token=None, base=BASE_URL, throttle=THROTTLE, label=""):
        self.token = token
        self.base = base
        self.throttle = throttle
        self.label = label
        self.me = {}

    def request(self, method, path, json_body=None, form=None, params=None, headers=None, timeout=60):
        url = self.base + path
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        hdrs = {"Accept": "application/json"}
        data = None
        if json_body is not None:
            data = json.dumps(json_body).encode()
            hdrs["Content-Type"] = "application/json"
        elif form is not None:
            data = urllib.parse.urlencode(form).encode()
            hdrs["Content-Type"] = "application/x-www-form-urlencoded"
        if self.token:
            hdrs["Authorization"] = "Bearer " + self.token
        hdrs.update(headers or {})
        if self.throttle:
            self.throttle.wait()
        REQUEST_COUNT[0] += 1
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        start = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                text = r.read().decode("utf-8", "replace")
                return Resp(method, url, r.status, text, dict(r.headers), time.monotonic() - start)
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", "replace")
            return Resp(method, url, e.code, text, dict(e.headers or {}), time.monotonic() - start)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            return Resp(method, url, 0, f"network error: {e}", {}, time.monotonic() - start)

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self.request("POST", path, json_body=body if body is not None else {}, **kw)

    def put(self, path, body=None, **kw):
        return self.request("PUT", path, json_body=body if body is not None else {}, **kw)

    def patch(self, path, body=None, **kw):
        return self.request("PATCH", path, json_body=body if body is not None else {}, **kw)

    def delete(self, path, **kw):
        return self.request("DELETE", path, **kw)


def must(resp, what):
    """Setup step that has to work; raises SetupError (the check then FAILs as a setup error)."""
    if not resp.ok:
        raise SetupError(f"{what}: {resp.short()}")
    return resp.body


class SetupError(Exception):
    pass


def items(body):
    """List endpoints answer with a bare list or {items|results|data|rows: [...]}."""
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        for key in ("items", "results", "data", "rows", "orders", "lines"):
            if isinstance(body.get(key), list):
                return body[key]
    return []


# ---------------------------------------------------------------- test orgs

def new_password():
    return "Gate-" + secrets.token_urlsafe(18)


def new_email(tag):
    return f"gate-{tag}-{secrets.token_hex(5)}@tekly.test"


def register_org(tag, country=None, region=None, extra=None):
    """Sign up a fresh shop. Waits out 429 (Retry-After) up to MAX_SIGNUP_WAIT_S.
    Returns a signed-in owner Api with .org (id, name) and .me (employee info)."""
    anon = Api()
    body = {
        "org_name": f"Gate {tag} {secrets.token_hex(3)}",
        "name": f"Owner {tag}",
        "email": new_email(tag),
        "password": new_password(),
        "accept_terms": True,
        "industry_template": "mobile_phone",
    }
    if country:
        body["country"] = country
    if region:
        body["region"] = region
    body.update(extra or {})
    waited = 0
    while True:
        r = anon.post("/api/auth/register", body)
        if r.status != 429:
            break
        retry = int(r.headers.get("Retry-After") or r.headers.get("retry-after") or 60)
        retry = min(max(retry, 5), 300)
        if waited + retry > MAX_SIGNUP_WAIT_S:
            raise SetupError(f"sign-up still rate-limited after {waited}s")
        log(f"  sign-up rate-limited (429); waiting {retry}s")
        time.sleep(retry)
        waited += retry
    must(r, "register org")
    owner = Api(r.body["access_token"], label=f"owner-{tag}")
    owner.me = {"employee_id": r.body["employee_id"], "org_id": r.body["org_id"], "name": body["name"]}
    owner.org_name = body["org_name"]
    owner.signup = r.body
    return owner


def login(email, password, label=""):
    anon = Api()
    r = anon.request("POST", "/api/auth/token", form={"username": email, "password": password})
    must(r, f"login {label}")
    api = Api(r.body["access_token"], label=label)
    api.me = {"employee_id": r.body["employee_id"], "org_id": r.body["org_id"], "role": r.body.get("role")}
    return api


def roles(owner):
    return items(must(owner.get("/api/staff/roles"), "list roles"))


def role_id(owner, *names):
    """First role whose name contains one of `names` (case-insensitive)."""
    rs = roles(owner)
    for want in names:
        for r in rs:
            if want.lower() in (r.get("name") or "").lower():
                return r["id"]
    raise SetupError(f"no role named like {names}: {[r.get('name') for r in rs]}")


def add_staff(owner, tag, role=None):
    """Create an employee with a generated password and sign them in."""
    email = new_email(tag)
    password = new_password()
    body = {"name": f"Staff {tag}", "email": email, "password": password}
    if role:
        body["role_id"] = role
    r = owner.post("/api/staff/employees", body)
    must(r, f"create employee {tag}")
    return login(email, password, label=tag)


# ---------------------------------------------------------------- concurrency

def race(n, fn):
    """Run fn(i) in n threads released together by a barrier. Returns the results in order."""
    barrier = threading.Barrier(n)
    results = [None] * n

    def worker(i):
        try:
            barrier.wait(timeout=30)
        except threading.BrokenBarrierError:
            pass
        try:
            results[i] = fn(i)
        except Exception as e:  # noqa: BLE001 — reported by the caller
            results[i] = e

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


# ---------------------------------------------------------------- check runner

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"


class Outcome(Exception):
    def __init__(self, status, reason):
        super().__init__(reason)
        self.status = status
        self.reason = reason


def fail(reason):
    raise Outcome(FAIL, reason)


def skip(reason):
    raise Outcome(SKIP, reason)


def check(cond, reason):
    """Assert a product behaviour: FAIL with `reason` when it does not hold."""
    if not cond:
        fail(reason)


def log(msg):
    print(msg, flush=True)


def run_checks(checks, only=None):
    """checks: list of (id, title, fn). fn() returns a PASS reason or raises Outcome.
    Prints one line per check and a summary; returns the exit code."""
    counts = {PASS: 0, FAIL: 0, SKIP: 0}
    failed = []
    for cid, title, fn in checks:
        if only and cid not in only:
            continue
        try:
            reason = fn() or "ok"
            status = PASS
        except Outcome as o:
            status, reason = o.status, o.reason
        except SetupError as e:
            status, reason = FAIL, f"setup error: {e}"
        except Exception as e:  # noqa: BLE001 — a harness bug shows as a FAIL with its traceback tail
            tb = traceback.format_exc().strip().splitlines()[-3:]
            status, reason = FAIL, f"harness error: {e!r} | {' / '.join(tb)}"
        counts[status] += 1
        if status == FAIL:
            failed.append(cid)
        log(f"{status:4}  {cid:9} {title} — {reason}")
    total = sum(counts.values())
    log("")
    log(f"SUMMARY {total} checks: {counts[PASS]} PASS, {counts[FAIL]} FAIL, {counts[SKIP]} SKIP"
        + (f"  (failed: {', '.join(failed)})" if failed else "")
        + f"  [{REQUEST_COUNT[0]} requests]")
    return 1 if counts[FAIL] else 0


def parse_only(argv):
    """`python regress.py F-3 W3-A-1` runs just those ids."""
    ids = [a for a in argv[1:] if not a.startswith("-")]
    return set(ids) or None


def money(x):
    try:
        return round(float(x), 2)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    sys.exit("gate.py is a library; run regress.py, isolation.py, countries.py or load.py")
