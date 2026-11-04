"""Load gate: 15 simulated users (5 shops x owner + 2 staff) for 60 s on the core endpoints.

    python tests-e2e/api_gate/load.py                  (TEKLY_URL=..., LOAD_SECONDS=60)

Setup (5 sign-ups, staff, a few parts and clients) runs under the normal request throttle;
the timed phase has no client-side throttle. Each user loops: think 0.2-0.6 s, then one action
picked for their role. Reports per-endpoint p50 / p95 / max, the 5xx count and requests/s.
Exit 1 when any request got a 5xx or any endpoint's p95 is 1 s or more.

Don't run this on a machine that is busy with another stress test: the numbers mean nothing.
"""
import os
import random
import sys
import threading
import time
import uuid

import regress
from gate import Api, add_staff, items, log, must, register_org

SHOPS = 5
SECONDS = int(os.environ.get("LOAD_SECONDS", "60"))
P95_LIMIT_S = 1.0


class Stats:
    def __init__(self):
        self.lock = threading.Lock()
        self.times = {}       # endpoint -> [seconds]
        self.codes = {}       # endpoint -> {status: n}

    def add(self, name, resp):
        with self.lock:
            self.times.setdefault(name, []).append(resp.elapsed)
            c = self.codes.setdefault(name, {})
            c[resp.status] = c.get(resp.status, 0) + 1


STATS = Stats()


def pct(values, p):
    v = sorted(values)
    if not v:
        return 0.0
    return v[min(len(v) - 1, int(round(p / 100 * (len(v) - 1))))]


def timed(api, name, method, path, body=None):
    r = api.request(method, path, json_body=body)
    STATS.add(name, r)
    return r


# ---------------------------------------------------------------- actions

def act_board(u):
    timed(u.api, "GET /api/orders/board", "GET", "/api/orders/board")


def act_list_orders(u):
    r = timed(u.api, "GET /api/orders", "GET", "/api/orders?limit=25")
    ids = [o["id"] for o in items(r.body)] if r.ok else []
    if ids:
        u.shop.orders.extend(ids[:3])


def act_open_order(u):
    if u.shop.orders:
        timed(u.api, "GET /api/orders/{id}", "GET", f"/api/orders/{random.choice(u.shop.orders)}")


def act_create_order(u):
    body = {"client_id": random.choice(u.shop.clients), "complaint": "load test",
            "lines": [regress.labour_line(random.choice([40, 60, 90]))]}
    r = timed(u.api, "POST /api/orders", "POST", "/api/orders", body)
    if r.ok:
        u.shop.orders.append(r.body["id"])


def act_add_line(u):
    if u.shop.orders:
        oid = random.choice(u.shop.orders)
        timed(u.api, "POST /api/orders/{id}/lines", "POST", f"/api/orders/{oid}/lines",
              regress.part_line(random.choice(u.shop.parts), qty=1, desc="Load part"))


def act_pay(u):
    if u.shop.orders:
        oid = random.choice(u.shop.orders)
        timed(u.api, "POST /api/cash/orders/{id}/pay", "POST", f"/api/cash/orders/{oid}/pay",
              {"method": "cash", "account_id": u.shop.fixture.cash, "amount": 5})


def act_clients(u):
    timed(u.api, "GET /api/clients", "GET", "/api/clients?limit=25")


def act_new_client(u):
    r = timed(u.api, "POST /api/clients", "POST", "/api/clients", {"name": f"Load {uuid.uuid4().hex[:6]}"})
    if r.ok:
        u.shop.clients.append(r.body["id"])


def act_parts(u):
    timed(u.api, "GET /api/inventory/parts", "GET", "/api/inventory/parts?limit=25")


def act_dashboard(u):
    timed(u.api, "GET /api/reports/dashboard", "GET", "/api/reports/dashboard")


ROLE_ACTIONS = {
    "owner": [act_dashboard, act_board, act_list_orders, act_open_order, act_create_order, act_pay, act_parts],
    "desk": [act_board, act_clients, act_new_client, act_create_order, act_open_order, act_pay, act_list_orders],
    "tech": [act_board, act_open_order, act_add_line, act_list_orders, act_parts],
}


# ---------------------------------------------------------------- setup

class LoadShop:
    def __init__(self, n):
        owner = register_org(f"load{n}", country="GB")
        self.fixture = regress.Shop(owner)
        mgr = must(owner.post("/api/settings/rbac/roles", {"name": "Load desk", "template": "Manager"}), "desk role")
        tech = must(owner.post("/api/settings/rbac/roles", {"name": "Load tech", "template": "Specialist"}), "tech role")
        self.people = {"owner": owner, "desk": add_staff(owner, f"desk{n}", mgr["id"]),
                       "tech": add_staff(owner, f"tech{n}", tech["id"])}
        self.parts = [self.fixture.part(qty=500) for _ in range(3)]
        self.clients = [must(owner.post("/api/clients", {"name": f"Load client {i}"}), "client")["id"] for i in range(3)]
        self.orders = [self.fixture.order([regress.labour_line(50)])["id"] for _ in range(3)]


class User:
    def __init__(self, shop, role):
        self.shop = shop
        self.role = role
        self.api = Api(shop.people[role].token, throttle=None)   # timed phase: no client-side throttle


def run_user(u, stop_at):
    rnd = random.Random()
    while time.monotonic() < stop_at:
        time.sleep(rnd.uniform(0.2, 0.6))
        rnd.choice(ROLE_ACTIONS[u.role])(u)


def main():
    log(f"Load gate: {SHOPS} shops x 3 users for {SECONDS}s")
    shops = [LoadShop(n) for n in range(1, SHOPS + 1)]
    users = [User(s, role) for s in shops for role in ("owner", "desk", "tech")]
    stop_at = time.monotonic() + SECONDS
    started = time.monotonic()
    threads = [threading.Thread(target=run_user, args=(u, stop_at)) for u in users]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.monotonic() - started

    total = sum(len(v) for v in STATS.times.values())
    errors_5xx = sum(n for c in STATS.codes.values() for code, n in c.items() if code >= 500 or code == 0)
    slow = []
    log("")
    log(f"{'endpoint':38} {'n':>5} {'p50 ms':>7} {'p95 ms':>7} {'max ms':>7}  statuses")
    for name in sorted(STATS.times):
        t = STATS.times[name]
        p95 = pct(t, 95)
        if p95 >= P95_LIMIT_S:
            slow.append(name)
        log(f"{name:38} {len(t):5} {pct(t, 50) * 1000:7.0f} {p95 * 1000:7.0f} {max(t) * 1000:7.0f}  {STATS.codes[name]}")
    log("")
    log(f"SUMMARY load: {total} requests in {elapsed:.0f}s = {total / elapsed:.1f} req/s, "
        f"5xx {errors_5xx}, endpoints with p95 >= {P95_LIMIT_S:.0f}s: {slow or 'none'}")
    return 1 if (errors_5xx or slow) else 0


if __name__ == "__main__":
    sys.exit(main())
