"""Load test: many shop staff using Tekly at once.

Creates SHOPS shops with USERS_PER_SHOP staff each (owner included), signs every one in, then
each simulated person works like real front-desk / bench staff for DURATION seconds: open the
job board, search customers, book a job, add a line, move it along, check inquiries and
reports. Pauses of 1–3 s between actions (set --think 0 for a flat-out stress run).

Every simulated person sends its own X-Forwarded-For address, so the per-address limits act as
they would for 100 real shops. Run the server with TRUSTED_PROXY_HOPS=1 so it reads that header.

  python scripts/loadtest.py --base http://127.0.0.1:8101 --shops 10 --users 10 --duration 180

Prints per-request counts, errors and response times (p50 / p95 / p99), and requests per second.
Only run it against a local or test server: it creates real shops, staff and jobs.
"""
import argparse
import asyncio
import random
import statistics
import string
import time
import uuid
from collections import defaultdict

import httpx

PASSWORD = "load-test-" + uuid.uuid4().hex[:12]   # throwaway, only for the shops this run creates

# What each shop has created, so people can try to reach other shops' records (leak probes).
SHOP_ORDERS: dict = defaultdict(set)
SHOP_CLIENTS: dict = defaultdict(set)
LEAKS: list = []


def leak(kind, detail):
    LEAKS.append((kind, detail))
    if len(LEAKS) <= 20:
        print(f"  !! LEAK {kind}: {detail}")


def foreign(registry, shop):
    others = [s for s in registry if s != shop and registry[s]]
    if not others:
        return None
    return random.choice(list(registry[random.choice(others)]))


def owner_of(registry, rid):
    return next((s for s, ids in registry.items() if rid in ids), None)


class Stats:
    def __init__(self):
        self.times = defaultdict(list)
        self.errors = defaultdict(lambda: defaultdict(int))

    def add(self, name, ms, status):
        self.times[name].append(ms)
        if (status >= 400 and not name.startswith("probe:")) or status == 0 or status >= 500:
            self.errors[name][status] += 1

    def ok_probe(self, name, status):
        pass


def pct(xs, p):
    if not xs:
        return 0.0
    xs = sorted(xs)
    k = min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))
    return xs[k]


async def call(client, stats, name, method, url, *, ip, token=None, **kw):
    headers = {"X-Forwarded-For": ip}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    t = time.perf_counter()
    try:
        r = await client.request(method, url, headers=headers, **kw)
        status = r.status_code
    except Exception:   # noqa: BLE001 — timeouts and refused connections count as errors
        r, status = None, 0
    stats.add(name, (time.perf_counter() - t) * 1000, status)
    return r


def fake_ip(i):
    return f"10.{(i // 65025) % 255}.{(i // 255) % 255}.{i % 255 + 1}"


async def setup_shop(client, stats, s, users):
    """One shop: register the owner, add staff, sign everyone in. Returns [(shop, token, ip)]."""
    ip = fake_ip(s * 1000)
    email = f"load-{uuid.uuid4().hex[:10]}@loadtest.dev"
    r = await call(client, stats, "setup: sign up", "POST", "/api/auth/register", ip=ip,
                   json={"org_name": f"Load Shop {s}", "name": "Owner", "email": email, "password": PASSWORD,
                         "accept_terms": True})
    if not r or r.status_code != 201:
        print(f"  shop {s}: sign-up failed ({r.status_code if r else 'no answer'}) {r.text[:120] if r else ''}")
        return []
    owner = r.json()["access_token"]
    people = [(s, owner, ip)]
    # Staff get the shop's manager role (or the first non-owner role) so they can book jobs.
    roles = await call(client, stats, "setup: roles", "GET", "/api/staff/roles", ip=ip, token=owner)
    items = roles.json() if roles is not None and roles.status_code == 200 else []
    items = items.get("items", items) if isinstance(items, dict) else items
    names = {(x.get("name") or "").lower(): x["id"] for x in items}
    role_id = names.get("manager") or next((i for n, i in names.items() if "owner" not in n and "admin" not in n), None)
    for u in range(1, users):
        e = f"staff-{uuid.uuid4().hex[:10]}@loadtest.dev"
        r = await call(client, stats, "setup: add staff", "POST", "/api/staff/employees", ip=ip, token=owner,
                       json={"name": f"Staff {u}", "email": e, "password": PASSWORD, "role_id": role_id})
        if not r or r.status_code not in (200, 201):
            print(f"  shop {s}: add staff failed ({r.status_code if r else 'no answer'}) {r.text[:120] if r else ''}")
            continue
        uip = fake_ip(s * 1000 + u)
        t = await call(client, stats, "setup: sign in", "POST", "/api/auth/token", ip=uip,
                       data={"username": e, "password": PASSWORD})
        if t is not None and t.status_code == 200 and "access_token" in t.json():
            people.append((s, t.json()["access_token"], uip))
        else:
            print(f"  shop {s}: sign in failed ({t.status_code if t else 'no answer'}) {t.text[:120] if t else ''}")
    return people


async def person(client, stats, shop, token, ip, stop_at, think):
    """One member of staff working until stop_at."""
    my_orders: list[str] = []

    async def pause():
        if think:
            await asyncio.sleep(random.uniform(think * 0.5, think * 1.5))

    while time.time() < stop_at:
        roll = random.random()
        if roll < 0.25:
            r = await call(client, stats, "job board", "GET", "/api/orders?limit=50", ip=ip, token=token)
            if r is not None and r.status_code == 200:
                for o in r.json().get("items", []):
                    who = owner_of(SHOP_ORDERS, o.get("id"))
                    if who is not None and who != shop:
                        leak("job board", f"shop {shop} saw shop {who}'s job {o.get('id')}")
        elif roll < 0.35:
            await call(client, stats, "me", "GET", "/api/auth/me", ip=ip, token=token)
        elif roll < 0.47:
            q = random.choice(["an", "ra", "si", "ku", "0"])
            r = await call(client, stats, "customer search", "GET", f"/api/clients?search={q}&limit=20", ip=ip, token=token)
            if r is not None and r.status_code == 200:
                for c in r.json().get("items", []):
                    who = owner_of(SHOP_CLIENTS, c.get("id"))
                    if who is not None and who != shop:
                        leak("customer search", f"shop {shop} found shop {who}'s customer {c.get('id')}")
        elif roll < 0.60:
            name = "Cust " + "".join(random.choices(string.ascii_letters, k=6))
            c = await call(client, stats, "new customer", "POST", "/api/clients", ip=ip, token=token,
                           json={"name": name, "phone": "+1555" + "".join(random.choices(string.digits, k=7))})
            if c is not None and c.status_code in (200, 201):
                SHOP_CLIENTS[shop].add(c.json()["id"])
                o = await call(client, stats, "new job", "POST", "/api/orders", ip=ip, token=token,
                               json={"client_id": c.json()["id"], "complaint": "Screen cracked, battery drains fast"})
                if o is not None and o.status_code in (200, 201):
                    my_orders.append(o.json()["id"])
                    SHOP_ORDERS[shop].add(o.json()["id"])
        elif roll < 0.70 and my_orders:
            oid = random.choice(my_orders)
            await call(client, stats, "add line", "POST", f"/api/orders/{oid}/lines", ip=ip, token=token,
                       json={"line_type": "labour", "kind": "service", "description": "Screen replacement",
                             "qty": 1, "unit_price": random.choice([49, 89, 129])})
        elif roll < 0.80 and my_orders:
            await call(client, stats, "open job", "GET", f"/api/orders/{random.choice(my_orders)}", ip=ip, token=token)
        elif roll < 0.86 and my_orders:
            oid = random.choice(my_orders)
            await call(client, stats, "change status", "PATCH", f"/api/orders/{oid}/status", ip=ip, token=token,
                       json={"status": random.choice(["in_repair", "diagnosed"])})
        elif roll < 0.90:
            await probe(client, stats, shop, token, ip)
        elif roll < 0.95:
            await call(client, stats, "inquiries", "GET", "/api/leads?limit=50", ip=ip, token=token)
        else:
            await call(client, stats, "profit report", "GET", "/api/reports/job-profitability", ip=ip, token=token)
        await pause()


async def probe(client, stats, shop, token, ip):
    """Try to reach another shop's records. Anything but 403/404 (or 422) is a leak."""
    oid, cid = foreign(SHOP_ORDERS, shop), foreign(SHOP_CLIENTS, shop)
    tries = []
    if oid:
        tries += [("probe: open job", "GET", f"/api/orders/{oid}", None),
                  ("probe: add line", "POST", f"/api/orders/{oid}/lines",
                   {"line_type": "labour", "description": "x", "qty": 1, "unit_price": 1}),
                  ("probe: status", "PATCH", f"/api/orders/{oid}/status", {"status": "in_repair"}),
                  ("probe: comment", "POST", f"/api/orders/{oid}/comments", {"body": "hello from another shop"}),
                  ("probe: pay ctx", "GET", f"/api/cash/orders/{oid}/payment-context", None)]
    if cid:
        tries += [("probe: customer", "GET", f"/api/clients/{cid}", None),
                  ("probe: edit cust", "PATCH", f"/api/clients/{cid}", {"name": "hijacked"}),
                  ("probe: new job", "POST", "/api/orders", {"client_id": cid, "complaint": "x"})]
    if not tries:
        return
    name, method, url, body = random.choice(tries)
    r = await call(client, stats, name, method, url, ip=ip, token=token, **({"json": body} if body else {}))
    if r is not None and r.status_code < 400:
        leak(name, f"shop {shop} -> {method} {url} = {r.status_code} {r.text[:100]}")
    elif r is not None and r.status_code in (403, 404, 405, 422):
        stats.ok_probe(name, r.status_code)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8101")
    ap.add_argument("--shops", type=int, default=10)
    ap.add_argument("--users", type=int, default=10, help="people per shop, owner included")
    ap.add_argument("--duration", type=int, default=120, help="seconds of steady load")
    ap.add_argument("--ramp", type=int, default=20, help="seconds to bring everyone in")
    ap.add_argument("--think", type=float, default=2.0, help="average pause between actions (0 = none)")
    a = ap.parse_args()

    limits = httpx.Limits(max_connections=a.shops * a.users + 20, max_keepalive_connections=a.shops * a.users)
    async with httpx.AsyncClient(base_url=a.base, timeout=30, limits=limits) as client:
        setup = Stats()
        print(f"Setting up {a.shops} shops x {a.users} people ...")
        t0 = time.time()
        groups = await asyncio.gather(*[setup_shop(client, setup, s, a.users) for s in range(a.shops)])
        people = [p for g in groups for p in g]
        print(f"  {len(people)} people signed in ({time.time() - t0:.0f}s)")

        stats = Stats()
        stop_at = time.time() + a.ramp + a.duration
        tasks = []
        for shop, tok, ip in people:
            await asyncio.sleep(a.ramp / max(1, len(people)))
            tasks.append(asyncio.create_task(person(client, stats, shop, tok, ip, stop_at, a.think)))
        print(f"Running {len(people)} people for {a.duration}s (pause {a.think}s) ...")
        t_run = time.time()
        await asyncio.gather(*tasks)
        elapsed = time.time() - t_run

    total = sum(len(v) for v in stats.times.values())
    errs = sum(sum(e.values()) for e in stats.errors.values())
    print(f"\n{total} requests in {elapsed:.0f}s = {total / elapsed:.1f} per second, "
          f"{errs} errors ({100 * errs / max(1, total):.2f}%)\n")
    print(f"{'request':18} {'count':>6} {'errors':>7} {'p50 ms':>8} {'p95 ms':>8} {'p99 ms':>8} {'max ms':>8}")
    for name in sorted(stats.times, key=lambda n: -pct(stats.times[n], 95)):
        xs = stats.times[name]
        e = stats.errors.get(name, {})
        print(f"{name:18} {len(xs):6} {sum(e.values()):7} {statistics.median(xs):8.0f} {pct(xs, 95):8.0f} "
              f"{pct(xs, 99):8.0f} {max(xs):8.0f}" + (f"   codes {dict(e)}" if e else ""))
    probes = sum(len(v) for k, v in stats.times.items() if k.startswith("probe:"))
    print(f"\nCross-shop probes: {probes} attempts, {len(LEAKS)} leaks"
          + ("" if not LEAKS else f"  <-- {sorted({k for k, _ in LEAKS})}"))
    if setup.errors:
        print("\nSetup errors:", {k: dict(v) for k, v in setup.errors.items()})


if __name__ == "__main__":
    asyncio.run(main())
