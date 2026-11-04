"""Regression gate: one live-API check per Critical/High/Medium QA finding.

Ids match the QA docs:
  QA-n      QA_multi-shop_2026-10-31.md, "Critical / High" table row n
  A-n..F-n  QA_fix-plan_2026-10-31.md, section A..F, n-th High/Medium bullet
  W3-X-n    QA_fix-plan_wave3.md, section W3-X, n-th High/Medium item (High first)

    python tests-e2e/api_gate/regress.py              # every check
    python tests-e2e/api_gate/regress.py QA-1 F-3     # just these
    TEKLY_URL=http://localhost:8090 python ...        # another server

Prints PASS / FAIL / SKIP per check with a one-line reason, then a summary line.
Exit code 1 when anything FAILs. W3-C and W3-E checks SKIP ("pending W3-C") instead of
failing while a probe shows that wave's fix is not on the server yet.
"""
import base64
import struct
import sys
import time
import uuid
import zlib

from gate import (
    PASS,  # noqa: F401  (re-exported for readers of this file)
    Api,
    Outcome,
    SetupError,
    add_staff,
    check,
    fail,
    items,
    log,
    login,
    money,
    must,
    new_email,
    new_password,
    parse_only,
    race,
    register_org,
    run_checks,
    skip,
)

ANON = Api()
RACE_N = 3


# ====================================================================== shop fixture

class Shop:
    """One test org plus the ids every check needs. Staff are created on first use."""

    def __init__(self, owner):
        self.owner = owner
        self.loc = must(owner.get("/api/auth/me"), "me")["location_id"]
        accounts = items(must(owner.get("/api/cash/accounts"), "cash accounts"))
        self.cash = next(a["id"] for a in accounts if a.get("payment_option") == "cash")
        self.card = next(a["id"] for a in accounts if a.get("payment_option") == "card")
        whs = items(must(owner.get("/api/settings/warehouses"), "warehouses"))
        self.warehouse = next(w["id"] for w in whs if w.get("location_id") == self.loc)
        self._staff = {}
        self._client = None
        self._supplier = None

    # ---- people
    def tech(self):
        """A technician: a role made from the 'Specialist' template."""
        if "tech" not in self._staff:
            r = must(self.owner.post("/api/settings/rbac/roles",
                                     {"name": f"Tech {uuid.uuid4().hex[:5]}", "template": "Specialist"}),
                     "create Specialist role")
            self._staff["tech"] = add_staff(self.owner, "tech", r["id"])
        return self._staff["tech"]

    def staff_with(self, tag, perms):
        """A person whose role grants exactly `perms` (no template)."""
        if tag not in self._staff:
            r = must(self.owner.post("/api/settings/rbac/roles",
                                     {"name": f"{tag} {uuid.uuid4().hex[:5]}",
                                      "permissions": {p: True for p in perms}}), f"create role {tag}")
            self._staff[tag] = add_staff(self.owner, tag, r["id"])
        return self._staff[tag]

    # ---- data
    def client(self):
        if not self._client:
            self._client = must(self.owner.post("/api/clients", {"name": "Gate Customer"}), "client")["id"]
        return self._client

    def supplier(self):
        if not self._supplier:
            self._supplier = must(self.owner.post("/api/suppliers", {"name": "Gate Supplier"}), "supplier")["id"]
        return self._supplier

    def location(self, name="Gate branch"):
        return must(self.owner.post("/api/staff/locations", {"name": f"{name} {uuid.uuid4().hex[:4]}"}),
                    "new location")["id"]

    def part(self, qty=10, cost=10.0, price=50.0, location=None, uom="pcs"):
        sku = "G" + uuid.uuid4().hex[:10].upper()
        pid = must(self.owner.post("/api/inventory/parts",
                                   {"name": f"Part {sku}", "sku": sku, "cost_price": cost,
                                    "sell_price": price, "uom": uom}), "create part")["id"]
        if qty:
            must(self.owner.post(f"/api/inventory/parts/{pid}/adjust",
                                 {"location_id": location or self.loc, "qty": qty, "unit_cost": cost}),
                 "stock part")
        return pid

    def qty(self, pid, location=None):
        body = must(self.owner.get(f"/api/inventory/parts/{pid}/stock"), "part stock")
        if location is None:
            return float(body.get("total_available") or 0)
        return float(sum(i.get("available") or 0 for i in body.get("items", []) if i.get("location_id") == location))

    def part_info(self, pid):
        return must(self.owner.get(f"/api/inventory/parts/{pid}"), "part")

    def order(self, lines=None, estimate=False, client=True):
        body = {"lines": lines or [], "is_estimate": estimate, "complaint": "gate check"}
        if client:
            body["client_id"] = self.client()
        return must(self.owner.post("/api/orders", body), "create order")

    def get_order(self, oid, who=None):
        return must((who or self.owner).get(f"/api/orders/{oid}"), "get order")

    def paid(self, oid):
        return float(must(self.owner.get(f"/api/cash/orders/{oid}/payment-context"), "payment context")
                     .get("already_paid") or 0)

    def balance(self, account_id):
        accts = items(must(self.owner.get("/api/cash/accounts"), "accounts"))
        return float(next(a for a in accts if a["id"] == account_id).get("balance") or 0)

    def set_status(self, oid, status, **extra):
        return self.owner.patch(f"/api/orders/{oid}/status", {"status": status, **extra})

    def close_paid(self, oid):
        """Pay the balance in cash and move the job to delivered."""
        r = self.owner.post(f"/api/cash/orders/{oid}/pay", {"method": "cash", "account_id": self.cash})
        if not r.ok and "settled" not in r.text:
            raise SetupError(f"pay: {r.short()}")
        return must(self.set_status(oid, "delivered"), "deliver job")


def part_line(pid, qty=1, price=50.0, desc="Part"):
    return {"kind": "product", "ref_id": pid, "description": desc, "qty": qty, "unit_price": price}


def labour_line(price=100.0, desc="Labour"):
    return {"kind": "service", "description": desc, "qty": 1, "unit_price": price}


def is_4xx(r):
    return 400 <= r.status < 500


def statuses(results):
    return [getattr(r, "status", repr(r)) for r in results]


# ====================================================================== context

class Ctx:
    """Test orgs, created on first use and reused by every check in the run."""

    def __init__(self):
        self._a = self._b = None
        self._pending = {}

    @property
    def a(self):
        if self._a is None:
            log("  (signing up shop A, GB)")
            self._a = Shop(register_org("a", country="GB"))
        return self._a

    @property
    def b(self):
        if self._b is None:
            log("  (signing up shop B, AU)")
            self._b = Shop(register_org("b", country="AU"))
        return self._b

    # ---- pending-wave probes (behaviour, not branch names)
    def pending(self, wave):
        if wave not in self._pending:
            self._pending[wave] = PROBES[wave](self)
        return self._pending[wave]


CTX = Ctx()


def probe_w3c(ctx):
    """W3-C fixed => a return against a job that never used the part is refused."""
    s = ctx.a
    pid = s.part(qty=1)
    o = s.order([labour_line(10)])
    r = s.owner.post("/api/returns", {"order_id": o["id"], "lines": [{"part_id": pid, "qty": 1, "restock": False}]})
    return r.ok


def probe_w3e(ctx):
    """W3-E fixed => the Specialist template no longer grants can_edit_closed_ticket."""
    r = must(ctx.a.owner.post("/api/settings/rbac/roles",
                              {"name": f"Probe {uuid.uuid4().hex[:5]}", "template": "Specialist"}), "probe role")
    perms = role_perms(ctx.a.owner, r["id"])
    return perms.get("can_edit_closed_ticket") is True


PROBES = {"W3-C": probe_w3c, "W3-E": probe_w3e}


def role_perms(owner, rid):
    for r in items(must(owner.get("/api/settings/rbac/roles"), "rbac roles")):
        if r["id"] == rid:
            return r.get("permissions") or {}
    raise SetupError(f"role {rid} not listed")


def wave(name, fn):
    """Wrap a check of a wave that may not be merged: a FAIL becomes SKIP while the wave is pending."""
    def run():
        try:
            return fn()
        except Outcome as o:
            if o.status == "FAIL" and CTX.pending(name):
                skip(f"pending {name} ({o.reason})")
            raise
    return run


# ====================================================================== QA table (31 Oct)

def qa_1():
    """Accept + Convert at once converts the estimate once and deducts stock once."""
    s = CTX.a
    pid = s.part(qty=5)
    est = s.order([part_line(pid)], estimate=True)
    oid, tok = est["id"], est["public_token"]
    before_orders = must(s.owner.get("/api/orders", params={"limit": 1}), "orders")["total"]

    def go(i):
        if i == 0:
            return ANON.post(f"/api/public/track/{tok}/respond", {"decision": "accept", "name": "Gate"})
        return s.owner.post(f"/api/orders/{oid}/convert")

    res = race(RACE_N, go)
    after_orders = must(s.owner.get("/api/orders", params={"limit": 1}), "orders")["total"]
    used = 5 - s.qty(pid)
    check(used == 1, f"stock deducted {used}x (want 1); responses {statuses(res)}")
    check(after_orders - before_orders <= 1, f"{after_orders - before_orders} new jobs from one estimate")
    return f"stock -1, {after_orders - before_orders} job(s) added, responses {statuses(res)}"


def qa_2():
    """An approved recommendation takes stock and cost; deleting its line returns exactly that."""
    s = CTX.a
    pid = s.part(qty=5, cost=12)
    o = s.order([labour_line(30)])
    rec = must(s.owner.post(f"/api/orders/{o['id']}/recommendations",
                            {"title": "Screen", "kind": "product", "ref_id": pid, "qty": 2, "unit_price": 50}), "rec")
    r = s.owner.post(f"/api/orders/{o['id']}/recommendations/{rec['id']}/approve-for-customer", {"how": "phone"})
    check(r.ok, f"approve failed: {r.short()}")
    line = next((ln for ln in s.get_order(o["id"])["lines"] if ln.get("ref_id") == pid), None)
    check(line is not None, "approved recommendation added no line")
    check(s.qty(pid) == 3, f"stock after approve {s.qty(pid)} (want 3)")
    check(money(line["cost_price"]) == 12, f"line cost_price {line['cost_price']} (want 12)")
    r = s.owner.delete(f"/api/orders/{o['id']}/lines/{line['id']}")
    check(r.ok, f"delete line: {r.short()}")
    check(s.qty(pid) == 5, f"stock after deleting the line {s.qty(pid)} (want 5, phantom stock if more)")
    return "stock 5 -> 3 -> 5, cost 12"


def qa_3():
    """Double-tap on recommendations/decide adds the line once; job total matches the lines."""
    s = CTX.a
    pid = s.part(qty=5)
    o = s.order([labour_line(30)])
    rec = must(s.owner.post(f"/api/orders/{o['id']}/recommendations",
                            {"title": "Case", "kind": "product", "ref_id": pid, "qty": 1, "unit_price": 20}), "rec")
    must(s.owner.post(f"/api/orders/{o['id']}/recommendations/{rec['id']}/publish"), "publish rec")
    res = race(RACE_N, lambda i: ANON.post(f"/api/public/track/{o['public_token']}/recommendations/decide",
                                           {"approve_ids": [rec["id"]], "name": "Gate"}))
    d = s.get_order(o["id"])
    n = sum(1 for ln in d["lines"] if ln.get("ref_id") == pid)
    lines_sum = round(sum(float(ln["total"]) for ln in d["lines"]), 2)
    check(n == 1, f"{n} lines added (want 1); responses {statuses(res)}")
    check(money(d["total"]) == lines_sum, f"order total {d['total']} != lines {lines_sum}")
    check(s.qty(pid) == 4, f"stock {s.qty(pid)} (want 4)")
    return f"1 line, total {d['total']} = lines"


def qa_4():
    """Double-click Pay / Prepay with the same Idempotency-Key posts once."""
    s = CTX.a
    o = s.order([labour_line(100)])
    key = "gate-" + uuid.uuid4().hex
    res = race(RACE_N, lambda i: s.owner.request(
        "POST", f"/api/cash/orders/{o['id']}/pay", json_body={"method": "cash", "account_id": s.cash, "amount": 40},
        headers={"Idempotency-Key": key}))
    check(s.paid(o["id"]) == 40, f"paid {s.paid(o['id'])} after 3 identical pays (want 40); {statuses(res)}")
    key = "gate-" + uuid.uuid4().hex
    o2 = s.order([labour_line(100)])
    res = race(RACE_N, lambda i: s.owner.request(
        "POST", f"/api/cash/orders/{o2['id']}/prepay", json_body={"amount": 25, "account_id": s.cash},
        headers={"Idempotency-Key": key}))
    got = s.paid(o2["id"])
    check(got == 25, f"prepaid {got} after 3 identical prepays (want 25); {statuses(res)}")
    return "pay 40 once, prepay 25 once"


def transfer_ready_to_transit(s, pid, qty, to_loc):
    t = must(s.owner.post("/api/transfers", {"from_location_id": s.loc, "to_location_id": to_loc,
                                             "lines": [{"part_id": pid, "qty": qty}]}), "create transfer")
    for st in ("ready", "in_transit"):
        must(s.owner.patch(f"/api/transfers/{t['id']}/status", {"status": st}), f"transfer -> {st}")
    return t["id"]


def qa_5():
    """Transfer completed/ready sent 3x at once credits the destination once and debits the source once."""
    s = CTX.a
    to_loc = s.location("Transfer dest")
    pid = s.part(qty=6)
    t = must(s.owner.post("/api/transfers", {"from_location_id": s.loc, "to_location_id": to_loc,
                                             "lines": [{"part_id": pid, "qty": 2}]}), "create transfer")
    res1 = race(RACE_N, lambda i: s.owner.patch(f"/api/transfers/{t['id']}/status", {"status": "ready"}))
    src = s.qty(pid, s.loc)
    check(src == 4, f"source {src} after 3x ready (want 4); {statuses(res1)}")
    must(s.owner.patch(f"/api/transfers/{t['id']}/status", {"status": "in_transit"}), "in transit")
    must(s.owner.patch(f"/api/transfers/{t['id']}/status", {"status": "received"}), "received")
    res2 = race(RACE_N, lambda i: s.owner.patch(f"/api/transfers/{t['id']}/status", {"status": "completed"}))
    dst, src = s.qty(pid, to_loc), s.qty(pid, s.loc)
    check(dst == 2 and src == 4, f"dest {dst} (want 2), source {src} (want 4); {statuses(res2)}")
    return "source -2 once, destination +2 once"


def stocktake_for(s, pid, qty):
    """A new location holding `qty` of the part, and an open stocktake there. Returns (loc, st_id, line)."""
    loc = s.location("Count room")
    must(s.owner.post(f"/api/inventory/parts/{pid}/adjust", {"location_id": loc, "qty": qty, "unit_cost": 10}),
         "stock count room")
    st = must(s.owner.post("/api/stocktake", {"location_id": loc}), "create stocktake")
    lines = items(must(s.owner.get(f"/api/stocktake/{st['id']}/lines"), "stocktake lines"))
    line = next(ln for ln in lines if ln["part_id"] == pid)
    return loc, st["id"], line


def qa_6():
    """Stocktake posted 3x at once applies the difference once."""
    s = CTX.a
    pid = s.part(qty=0)
    loc, st, line = stocktake_for(s, pid, 5)
    must(s.owner.patch(f"/api/stocktake/{st}/lines/{line['id']}", {"counted_qty": 3}), "count")
    res = race(RACE_N, lambda i: s.owner.post(f"/api/stocktake/{st}/post"))
    got = s.qty(pid, loc)
    check(got == 3, f"stock {got} after 3x post (want 3); {statuses(res)}")
    return f"stock 5 -> 3 once; {statuses(res)}"


def qa_7():
    """Two staff edit the same line at once: no lost update; stock and total agree."""
    s = CTX.a
    pid = s.part(qty=10)
    o = s.order([part_line(pid, qty=1, price=50)])
    lid = o["lines"][0]["id"]
    who = [s.tech(), s.owner]   # two people: a technician changes qty, the owner changes the price
    res = race(2, lambda i: who[i].patch(f"/api/orders/{o['id']}/lines/{lid}",
                                         [{"qty": 3}, {"unit_price": 70}][i]))
    d = s.get_order(o["id"])
    ln = next(x for x in d["lines"] if x["id"] == lid)
    used = 10 - s.qty(pid)
    check(used == ln["qty"], f"stock used {used} but line qty {ln['qty']}; {statuses(res)}")
    check(money(ln["total"]) == money(ln["qty"] * ln["unit_price"]), f"line total {ln['total']} != qty x price")
    check(money(d["total"]) == money(sum(x["total"] for x in d["lines"])), f"job total {d['total']} != lines")
    oks = [r.ok for r in res]
    if all(oks):
        check(ln["qty"] == 3 and money(ln["unit_price"]) == 70,
              f"lost update: both edits returned 200 but line is qty {ln['qty']} price {ln['unit_price']}")
    # qty + qty
    res = race(2, lambda i: who[i].patch(f"/api/orders/{o['id']}/lines/{lid}", {"qty": [4, 5][i]}))
    ln = next(x for x in s.get_order(o["id"])["lines"] if x["id"] == lid)
    used = 10 - s.qty(pid)
    check(used == ln["qty"], f"qty+qty: stock used {used} but line qty {ln['qty']}; {statuses(res)}")
    return f"line qty {ln['qty']} = stock used {used}"


def qa_8():
    """The same line removed 3x at once returns its stock once."""
    s = CTX.a
    pid = s.part(qty=5)
    o = s.order([part_line(pid, qty=2)])
    lid = o["lines"][0]["id"]
    check(s.qty(pid) == 3, f"stock after line {s.qty(pid)} (want 3)")
    res = race(RACE_N, lambda i: s.owner.delete(f"/api/orders/{o['id']}/lines/{lid}"))
    check(s.qty(pid) == 5, f"stock {s.qty(pid)} after 3x delete (want 5); {statuses(res)}")
    return f"stock back to 5 once; {statuses(res)}"


def qa_9():
    """One job gets one full invoice (3 parallel issues, and prepay + invoice)."""
    s = CTX.a
    o = s.order([labour_line(100)])
    res = race(RACE_N, lambda i: s.owner.post(f"/api/cash/orders/{o['id']}/invoices", {}))
    invs = must(s.owner.get(f"/api/cash/orders/{o['id']}/invoices"), "invoices")["invoices"]
    check(len(invs) == 1, f"{len(invs)} invoices for one job (want 1); {statuses(res)}")
    o2 = s.order([labour_line(100)])
    must(s.owner.post(f"/api/cash/orders/{o2['id']}/prepay", {"amount": 20, "account_id": s.cash}), "prepay")
    s.owner.post(f"/api/cash/orders/{o2['id']}/invoices", {})
    s.owner.post(f"/api/cash/orders/{o2['id']}/invoices", {})
    invs2 = must(s.owner.get(f"/api/cash/orders/{o2['id']}/invoices"), "invoices")["invoices"]
    billed = sum(float(i["total"]) for i in invs2 if i.get("status") != "cancelled")
    check(billed <= 100.001, f"prepay + invoice billed {billed} on a 100 job ({len(invs2)} invoices)")
    return f"1 invoice; prepay job billed {billed}"


def qa_10():
    """A new role sent with a few permissions gets only those."""
    s = CTX.a
    sent = {"can_create_tickets": True, "can_view_client_info": True}
    r = must(s.owner.post("/api/settings/rbac/roles", {"name": f"Few {uuid.uuid4().hex[:5]}", "permissions": sent}),
             "create role")
    on = sorted(k for k, v in role_perms(s.owner, r["id"]).items() if v is True)
    extra = [k for k in on if k not in sent]
    check(not extra, f"{len(extra)} permissions switched on that were not sent, e.g. {extra[:5]}")
    return f"only {on}"


def default_tax_change(s):
    """Make a new 8% tax the default; returns (new tax id, old default id). Caller restores."""
    taxes = items(must(s.owner.get("/api/catalog/taxes"), "taxes"))
    old = next(t for t in taxes if t.get("is_default"))
    new = must(s.owner.post("/api/catalog/taxes", {"name": "Gate tax 8", "rate": 8, "mode": "imposed"}), "new tax")
    must(s.owner.put(f"/api/catalog/taxes/{new['id']}", {"name": "Gate tax 8", "rate": 8, "mode": "imposed",
                                                          "is_default": True}), "make default")
    return new["id"], old


def restore_tax(s, old):
    s.owner.put(f"/api/catalog/taxes/{old['id']}", {"name": old["name"], "rate": old["rate"], "mode": old["mode"],
                                                     "is_default": True})


def qa_11():
    """Making another tax the default changes what new jobs charge."""
    s = CTX.a
    new_id, old = default_tax_change(s)
    try:
        o = s.order([labour_line(100)])
        rate = o["lines"][0].get("tax_rate")
        check(money(rate) == 8, f"new job line tax_rate {rate} after making 8% the default (old {old['rate']})")
        return "new job charges the new default 8%"
    finally:
        restore_tax(s, old)


def qa_12():
    """Customer cannot add recommendations to a delivered, paid job, nor accept a declined estimate."""
    s = CTX.a
    pid = s.part(qty=5)
    o = s.order([labour_line(40)])
    rec = must(s.owner.post(f"/api/orders/{o['id']}/recommendations",
                            {"title": "Extra", "kind": "product", "ref_id": pid, "qty": 1, "unit_price": 20}), "rec")
    must(s.owner.post(f"/api/orders/{o['id']}/recommendations/{rec['id']}/publish"), "publish")
    s.close_paid(o["id"])
    r = ANON.post(f"/api/public/track/{o['public_token']}/recommendations/decide",
                  {"approve_ids": [rec["id"]], "name": "Gate"})
    d = s.get_order(o["id"])
    check(len(d["lines"]) == 1, f"customer added a line to a delivered paid job: {r.short()}")
    est = s.order([labour_line(50)], estimate=True)
    must(ANON.post(f"/api/public/track/{est['public_token']}/respond", {"decision": "decline", "reason": "no"}),
         "decline")
    r2 = ANON.post(f"/api/public/track/{est['public_token']}/respond", {"decision": "accept", "name": "Gate"})
    e = s.get_order(est["id"])
    check(e["is_estimate"] and not r2.ok, f"declined estimate accepted: {r2.short()}")
    return f"decide on delivered job -> {r.status}, accept after decline -> {r2.status}"


def sale(s, pid, qty=2, price=60.0, desc="Gate item"):
    sd = must(s.owner.post("/api/sales", {"account_id": s.cash, "lines": [
        {"kind": "product", "ref_id": pid, "qty": qty, "unit_price": price, "description": desc}]}), "till sale")
    full = must(s.owner.get(f"/api/sales/{sd['id']}"), "sale")
    return full


def qa_13():
    """A counter return sent twice refunds and restocks once."""
    s = CTX.a
    pid = s.part(qty=5)
    sd = sale(s, pid, qty=2)
    lid = sd["lines"][0]["id"]
    cash0 = s.balance(s.cash)
    r1 = s.owner.post(f"/api/sales/{sd['id']}/returns", {"lines": [{"line_id": lid, "qty": 2}], "account_id": s.cash})
    time.sleep(1)
    r2 = s.owner.post(f"/api/sales/{sd['id']}/returns", {"lines": [{"line_id": lid, "qty": 2}], "account_id": s.cash})
    refunded = round(cash0 - s.balance(s.cash), 2)
    check(s.qty(pid) == 5, f"stock {s.qty(pid)} after return sent twice (want 5); {r1.status}/{r2.status}")
    check(refunded == 120, f"refunded {refunded} (want 120); {r1.status}/{r2.status}")
    return f"restocked once, refunded 120; second -> {r2.status}"


def qa_14():
    """Concurrent PO receive adds stock once; double product create makes one SKU."""
    s = CTX.a
    pid = s.part(qty=0)
    po = must(s.owner.post("/api/inventory/po", {"supplier_id": s.supplier(), "location_id": s.loc,
                                                 "lines": [{"part_id": pid, "qty_ordered": 4, "unit_cost": 11}]}), "PO")
    po_id = po.get("id") or po.get("po_id")
    pl = must(s.owner.get(f"/api/inventory/po/{po_id}"), "PO detail")["lines"][0]["id"]
    res = race(RACE_N, lambda i: s.owner.post(f"/api/inventory/po/{po_id}/receive",
                                              {"lines": [{"po_line_id": pl, "qty": 4}]}))
    check(s.qty(pid) == 4, f"stock {s.qty(pid)} after 3x receive of 4 (want 4); {statuses(res)}")
    sku = "DUP" + uuid.uuid4().hex[:8].upper()
    res2 = race(RACE_N, lambda i: s.owner.post("/api/catalog/products", {"name": "Dup", "sku": sku}))
    made = sum(1 for r in res2 if r.ok)
    check(made == 1, f"{made} products created with SKU {sku}; {statuses(res2)}")
    return f"receive once; 1 SKU ({statuses(res2)})"


def qa_15():
    """A work order refuses invoice/PO/estimate statuses and unknown text."""
    s = CTX.a
    o = s.order([labour_line(10)])
    bad = {}
    for st in ("bogus_status", "inv_paid", "po_draft", "est_draft"):
        r = s.set_status(o["id"], st)
        if not is_4xx(r):
            bad[st] = r.status
    now = s.get_order(o["id"])["status"]
    check(not bad, f"accepted statuses {bad}; job now '{now}'")
    check(now == "new", f"job status changed to {now}")
    return "all four refused"


# ====================================================================== A roles

def a_1():
    """POST /api/staff/roles with a partial map turns on only what was sent."""
    s = CTX.a
    r = must(s.owner.post("/api/staff/roles", {"name": f"Partial {uuid.uuid4().hex[:5]}",
                                               "permissions": {"can_create_tickets": True}}), "create role")
    on = sorted(k for k, v in role_perms(s.owner, r["id"]).items() if v is True)
    extra = [k for k in on if k != "can_create_tickets"]
    check(not extra, f"{len(extra)} extra permissions on, e.g. {extra[:5]}")
    return f"on: {on}"


def a_2():
    """A technician cannot see cash balances, invoices, or stock value at cost."""
    s = CTX.a
    tech = s.tech()
    pid = s.part(qty=3, cost=10)
    leaks = []
    r = tech.get("/api/cash/balances")
    if r.ok and "balance" in r.text:
        leaks.append(f"cash/balances {r.status}")
    r = tech.get("/api/cash/accounts")
    if r.ok and any(a.get("balance") not in (None, 0, 0.0) for a in items(r.body)):
        leaks.append("cash/accounts balances")
    r = tech.get("/api/finance/invoices")
    if r.ok and items(r.body):
        leaks.append(f"finance/invoices {len(items(r.body))} rows")
    r = tech.get(f"/api/inventory/parts/{pid}/stock")
    if r.ok and r.get("total_value") not in (None, 0):
        leaks.append(f"parts stock total_value={r.get('total_value')}")
    check(not leaks, "technician sees: " + "; ".join(leaks))
    return "balances, invoices and stock value hidden"


def a_3():
    """A technician cannot close an unpaid job; their cost-price edit is refused, not silently ignored."""
    s = CTX.a
    tech = s.tech()
    o = s.order([labour_line(80)])
    r = tech.patch(f"/api/orders/{o['id']}/status", {"status": "delivered"})
    now = s.get_order(o["id"])["status"]
    check(now != "delivered", f"technician delivered an unpaid job: {r.short()}")
    pid = s.part(qty=3, cost=10)
    o2 = s.order([part_line(pid)])
    lid = o2["lines"][0]["id"]
    r2 = tech.patch(f"/api/orders/{o2['id']}/lines/{lid}", {"cost_price": 1})
    cost = next(x for x in s.get_order(o2["id"])["lines"] if x["id"] == lid)["cost_price"]
    check(not (r2.ok and money(cost) != 1), f"cost edit returned {r2.status} but cost stayed {cost} (silent ignore)")
    check(money(cost) != 1, f"technician changed cost price to 1: {r2.short()}")
    return f"close -> {r.status}, cost edit -> {r2.status}"


# ====================================================================== B tax, coupons, pricing

def b_1():
    """Making a tax the default moves the tax-rules stack with it."""
    s = CTX.a
    new_id, old = default_tax_change(s)
    try:
        stacks = must(s.owner.get("/api/settings/tax-rules"), "tax rules").get("stacks", {})
        stack = stacks.get("tickets_sales") or []
        check(new_id in stack, f"tickets_sales stack {stack} doesn't include the new default {new_id}")
        return "stack follows the default"
    finally:
        restore_tax(s, old)


def b_2():
    """Coupons over 100% are refused, and a coupon is taken off before tax (same tax as the job)."""
    s = CTX.a
    r = s.owner.post("/api/coupons", {"code": "G150" + uuid.uuid4().hex[:4].upper(), "kind": "pct", "value": 150})
    check(is_4xx(r), f"150% coupon accepted: {r.short()}")
    code = "G10" + uuid.uuid4().hex[:5].upper()
    must(s.owner.post("/api/coupons", {"code": code, "kind": "pct", "value": 10}), "coupon")
    o = s.order([labour_line(100)])
    must(s.owner.post(f"/api/orders/{o['id']}/apply-coupon", {"code": code}), "apply coupon")
    lines = s.get_order(o["id"])["lines"]
    job_rate = lines[0].get("tax_rate")
    coupon = next((x for x in lines if "coupon" in (x.get("description") or "").lower()), None)
    check(coupon is not None, "no coupon line")
    check(money(coupon.get("tax_rate")) == money(job_rate),
          f"coupon line tax_rate {coupon.get('tax_rate')} vs job {job_rate}: tax over-collected")
    return f"150% -> {r.status}; coupon taxed at {coupon.get('tax_rate')}%"


def b_3():
    """Tax rate 150 / -7 / mode 'sideways' and pricing price_decimals -3 / margin_guard 'banana' are refused."""
    s = CTX.a
    bad = []
    for body in ({"name": "T150", "rate": 150}, {"name": "Tneg", "rate": -7}, {"name": "Tside", "rate": 5, "mode": "sideways"}):
        r = s.owner.post("/api/catalog/taxes", body)
        if not is_4xx(r):
            bad.append(f"tax {body} -> {r.status}")
    for body in ({"price_decimals": -3}, {"margin_guard": "banana"}):
        r = s.owner.put("/api/pricing/defaults", body)
        if not is_4xx(r):
            bad.append(f"pricing {body} -> {r.status}")
    check(not bad, "; ".join(bad))
    return "all five refused"


def b_4():
    """Service negative price / negative duration / 1e12 price refused; commissions keep their type, 150% refused."""
    s = CTX.a
    bad = []
    for body in ({"name": "Neg", "price": -5}, {"name": "NegDur", "price": 5, "duration_min": -10},
                 {"name": "Huge", "price": 1e12}):
        r = s.owner.post("/api/catalog/services", body)
        if not is_4xx(r):
            bad.append(f"service {body} -> {r.status}")
    svc = must(s.owner.post("/api/catalog/services", {"name": "Gate svc", "price": 50}), "service")["id"]
    r = s.owner.post(f"/api/catalog/services/{svc}/commissions", {"calc_type": "fixed", "amount": 5})
    if r.ok:
        rules = items(must(s.owner.get(f"/api/catalog/services/{svc}/commissions"), "commissions"))
        types = [x.get("calc_type") for x in rules]
        if "percent" in types:
            bad.append(f"commission 'fixed' saved as {types}")
    r = s.owner.post(f"/api/catalog/services/{svc}/commissions", {"calc_type": "percent", "amount": 150})
    if not is_4xx(r):
        bad.append(f"commission 150% -> {r.status}")
    check(not bad, "; ".join(bad))
    return "bad services and commissions refused"


def b_5():
    """invoice-preview applies line discounts."""
    s = CTX.a
    o = s.order([dict(labour_line(100), discount=10, discount_type="pct")])
    pv = must(s.owner.get(f"/api/orders/{o['id']}/invoice-preview"), "invoice preview")
    check(money(pv["total"]) == 90, f"preview total {pv['total']} (want 90 after 10% line discount)")
    check(money(pv["lines"][0]["total"]) == 90, f"preview line total {pv['lines'][0]['total']} (want 90)")
    return "preview total 90"


# ====================================================================== C reports

# /api/reports/dashboard is cached for 30 s per server process (by design), so a dashboard
# figure is only read DASH_WAIT seconds after the last change it should (or shouldn't) include.
DASH_WAIT = 31


def revenue_views(s, settle=True):
    if settle:
        time.sleep(DASH_WAIT)
    dash = must(s.owner.get("/api/reports/dashboard"), "dashboard")["stats"]
    comp = must(s.owner.get("/api/reports/company-dashboard"), "company dashboard")
    fin = must(s.owner.get("/api/finance/reports/summary"), "finance summary")
    tp = must(s.owner.get("/api/reports/total-profit"), "total profit")["totals"]
    return {
        "dashboard.revenue": float(dash.get("revenue") or 0),
        "company.today.revenue": float(comp["today"].get("revenue") or 0),
        "finance.total_revenue": float(fin.get("total_revenue") or 0),
        "total_profit.revenue": float(tp.get("revenue") or 0),
        "dashboard.outstanding": float(dash.get("outstanding") or 0),
        "company.receivables": float(comp["kpis"].get("receivables") or 0),
        "finance.total_outstanding": float(fin.get("total_outstanding") or 0),
        "company.today.refunds": float(comp["today"].get("refunds") or 0),
        "company.today.cash_collected": float(comp["today"].get("cash_collected") or 0),
    }


def deltas(before, after, keys):
    return {k: round(after[k] - before[k], 2) for k in keys}


_C_FIX = {}


def c_fixture():
    """One paid, delivered job of 120 (VAT 20% included = 100 net) and one unpaid delivered job of 70,
    with every revenue/receivables figure read before and after."""
    if not _C_FIX:
        s = CTX.a
        before = revenue_views(s)
        paid = s.order([labour_line(120)])
        s.close_paid(paid["id"])
        unpaid = s.order([labour_line(70)])
        must(s.set_status(unpaid["id"], "delivered", allow_unpaid=True), "deliver unpaid")
        _C_FIX.update(before=before, after=revenue_views(s))
    return _C_FIX


REV_KEYS = ["dashboard.revenue", "company.today.revenue", "finance.total_revenue", "total_profit.revenue"]
AR_KEYS = ["dashboard.outstanding", "company.receivables", "finance.total_outstanding"]


def c_1():
    """The same day's revenue is the same number on every screen."""
    f = c_fixture()
    d = deltas(f["before"], f["after"], REV_KEYS)
    check(len(set(d.values())) == 1, f"a paid job of 120 (100 net) + an unpaid one of 70 moved revenue by {d}")
    return f"every screen +{list(d.values())[0]}"


def c_2():
    """Dashboard revenue is not 0 after money was collected on a closed job."""
    f = c_fixture()
    d = deltas(f["before"], f["after"], ["dashboard.revenue", "company.today.revenue"])
    check(all(v > 0 for v in d.values()), f"revenue deltas {d} after a paid, delivered job")
    return f"{d}"


def c_3():
    """Total profit books till sales ex-tax and subtracts returns."""
    s = CTX.a
    pid = s.part(qty=5, cost=10)
    b = must(s.owner.get("/api/reports/total-profit"), "tp")["buckets"]["sales"]
    sd = sale(s, pid, qty=2, price=60)   # 120 incl. 20% VAT = 100 net
    must(s.owner.post(f"/api/sales/{sd['id']}/returns",
                      {"lines": [{"line_id": sd["lines"][0]["id"], "qty": 1}], "account_id": s.cash}), "return 1")
    a = must(s.owner.get("/api/reports/total-profit"), "tp")["buckets"]["sales"]
    rev = round(a["revenue"] - b["revenue"], 2)
    tax = round(a["tax"] - b["tax"], 2)
    check(rev == 50 and tax == 10, f"sale 2x60 incl. VAT, 1 returned: revenue +{rev} (want 50), tax +{tax} (want 10)")
    return "revenue +50 net, tax +10"


def c_4():
    """Receivables: one unpaid delivered job shows the same amount everywhere."""
    f = c_fixture()
    d = deltas(f["before"], f["after"], AR_KEYS)
    check(set(d.values()) == {70.0}, f"an unpaid delivered job of 70 moved receivables by {d}")
    return "all +70"


# ====================================================================== D stock cost

def d_1():
    """Opening stock via adjust takes the sent unit_cost; plain receive without cost keeps a cost."""
    s = CTX.a
    pid = s.part(qty=0, cost=9)
    must(s.owner.post(f"/api/inventory/parts/{pid}/adjust", {"location_id": s.loc, "qty": 4, "unit_cost": 7}), "adjust")
    p = s.part_info(pid)
    check(money(p["avg_cost"]) == 7, f"avg_cost {p['avg_cost']} after opening stock at 7")
    pid2 = s.part(qty=0, cost=9)
    must(s.owner.post(f"/api/inventory/parts/{pid2}/adjust", {"location_id": s.loc, "qty": 2}), "adjust no cost")
    p2 = s.part_info(pid2)
    check(float(p2["avg_cost"] or 0) > 0, f"receive without unit_cost left avg_cost {p2['avg_cost']} (catalog cost 9)")
    return f"avg_cost 7; no-cost receive -> {p2['avg_cost']}"


def d_2():
    """Archiving a part that has stock is refused (bulk set_active=0 and PUT active:false)."""
    s = CTX.a
    pid = s.part(qty=3)
    r = s.owner.post("/api/inventory/parts/bulk", {"part_ids": [pid], "action": "set_active", "value": "0"})
    p = s.part_info(pid)
    archived = p.get("active") is False
    check(not (r.ok and archived), f"bulk set_active=0 archived a part with 3 in stock: {r.short()}")
    r2 = s.owner.put(f"/api/inventory/parts/{pid}", {"name": p["name"], "active": False})
    p2 = s.part_info(pid)
    if r2.ok and p2.get("active") is not False:
        fail(f"PUT active:false -> {r2.status} but part still active (silently ignored)")
    return f"bulk -> {r.status}, PUT -> {r2.status}"


def d_3():
    """A write-off larger than stock is refused; catalog write-off logs a negative movement."""
    s = CTX.a
    pid = s.part(qty=2)
    r = s.owner.post("/api/stock-docs/write-offs", {"location_id": s.loc, "lines": [{"part_id": pid, "qty": 5}]})
    check(is_4xx(r) and s.qty(pid) == 2, f"write-off 5 of 2: {r.short()}; stock {s.qty(pid)}")
    r = s.owner.post(f"/api/catalog/products/{pid}/write-off", {"location_id": s.loc, "qty": 1})
    check(r.ok, f"catalog write-off: {r.short()}")
    mv = items(must(s.owner.get(f"/api/inventory/parts/{pid}/movements"), "movements"))
    wo = [m for m in mv if "write" in (m.get("movement_type") or m.get("type") or "")]
    check(wo and all(float(m.get("qty") or 0) < 0 for m in wo), f"write-off movements {wo[:2]}")
    return "over-write-off refused, movement negative"


def d_4():
    """A free PO line (unit_cost 0) is costed at 0, not the catalog price."""
    s = CTX.a
    pid = s.part(qty=0, cost=25)
    po = must(s.owner.post("/api/inventory/po", {"supplier_id": s.supplier(), "location_id": s.loc,
                                                 "lines": [{"part_id": pid, "qty_ordered": 2, "unit_cost": 0}]}), "PO")
    po_id = po.get("id") or po.get("po_id")
    pl = must(s.owner.get(f"/api/inventory/po/{po_id}"), "PO")["lines"][0]["id"]
    must(s.owner.post(f"/api/inventory/po/{po_id}/receive", {"lines": [{"po_line_id": pl, "qty": 2}]}), "receive")
    avg = s.part_info(pid)["avg_cost"]
    check(money(avg) == 0, f"free PO units costed at {avg} (catalog cost 25)")
    return "free units cost 0"


def d_5():
    """PO receipts land in the location's warehouse (no stock split into a '—' row)."""
    s = CTX.a
    pid = s.part(qty=0)
    po = must(s.owner.post("/api/inventory/po", {"supplier_id": s.supplier(), "location_id": s.loc,
                                                 "lines": [{"part_id": pid, "qty_ordered": 1, "unit_cost": 5}]}), "PO")
    po_id = po.get("id") or po.get("po_id")
    pl = must(s.owner.get(f"/api/inventory/po/{po_id}"), "PO")["lines"][0]["id"]
    must(s.owner.post(f"/api/inventory/po/{po_id}/receive", {"lines": [{"po_line_id": pl, "qty": 1}]}), "receive")
    must(s.owner.post(f"/api/inventory/parts/{pid}/adjust", {"location_id": s.loc, "qty": 1, "unit_cost": 5}), "adjust")
    rows_ = must(s.owner.get(f"/api/inventory/parts/{pid}/stock"), "stock")["items"]
    here = [r for r in rows_ if r.get("location_id") == s.loc]
    check(all(r.get("warehouse_id") for r in here), f"stock rows without a warehouse: {here}")
    check(len(here) == 1, f"one location's stock split over {len(here)} rows: {here}")
    return f"1 row in warehouse {here[0]['warehouse_id'][:8]}"


# ====================================================================== E validation

def e_1():
    """Unknown or wrong-kind ids give 404/422, never 500."""
    s = CTX.a
    pid = s.part(qty=1)
    probes = [
        s.owner.post(f"/api/inventory/parts/{pid}/adjust", {"location_id": s.warehouse, "qty": 1}),
        s.owner.post("/api/inventory/po", {"supplier_id": s.supplier(), "location_id": s.warehouse,
                                           "lines": [{"part_id": pid, "qty_ordered": 1}]}),
        s.owner.put("/api/settings/tax-rules", {"doc_family": "tickets_sales", "tax_ids": [str(uuid.uuid4())]}),
        s.owner.post("/api/catalog/products/bulk", {"ids": [pid], "product_ids": [pid], "action": "set_category",
                                                    "category_id": str(uuid.uuid4()), "value": str(uuid.uuid4())}),
        s.owner.post("/api/settings/warehouses", {"name": "W", "location_id": str(uuid.uuid4())}),
    ]
    bad = [r.short(120) for r in probes if r.status >= 500 or r.ok]
    check(not bad, "; ".join(bad))
    return "statuses " + str([r.status for r in probes])


def e_2():
    """With prohibit_dup_phone on, a differently formatted duplicate phone is refused (not 500, not created)."""
    s = CTX.a
    must(s.owner.put("/api/staff/company", {"prohibit_dup_phone": True}), "turn on dup check")
    try:
        num = "7700" + str(uuid.uuid4().int)[:6]
        must(s.owner.post("/api/clients", {"name": "Dup One", "phone": f"+44{num}"}), "first client")
        r = s.owner.post("/api/clients", {"name": "Dup Two", "phone": f"+44 {num[:4]} {num[4:]}"})
        check(r.status < 500, f"duplicate phone -> {r.short()}")
        check(not r.ok, f"formatted duplicate phone accepted: {r.short()}")
        return f"duplicate -> {r.status}"
    finally:
        s.owner.put("/api/staff/company", {"prohibit_dup_phone": False})


def e_3():
    """Company currency 'XYZ' and timezone 'Mars/Olympus' are refused."""
    s = CTX.a
    r1 = s.owner.put("/api/staff/company", {"currency_code": "XYZ"})
    r2 = s.owner.put("/api/staff/company", {"timezone": "Mars/Olympus"})
    co = must(s.owner.get("/api/staff/company"), "company")
    check(is_4xx(r1) and is_4xx(r2), f"currency XYZ -> {r1.status}, timezone Mars -> {r2.status}")
    check(co.get("currency_code") != "XYZ" and co.get("timezone") != "Mars/Olympus", "bad values stored")
    return "both refused"


def e_4():
    """Numbering: {YYYY} refused or rendered, prefix-only change accepted, preview works."""
    s = CTX.a
    bad = []
    r = s.owner.post("/api/settings/numbering/preview", {"doc_type": "order", "template": "WO-{C:5}"})
    if not r.ok:
        bad.append(f"preview -> {r.status}")
    r = s.owner.post("/api/settings/numbering/preview", {"doc_type": "order", "template": "X-{YYYY}-{C:4}"})
    if r.ok and "{YYYY}" in r.text:
        bad.append("{YYYY} shown literally")
    cur = next(i for i in items(must(s.owner.get("/api/settings/numbering"), "numbering")) if i["doc_type"] == "sale")
    r = s.owner.put("/api/settings/numbering", {"doc_type": "sale", "template": cur["template"].replace("S-", "SL-", 1)})
    if not r.ok:
        bad.append(f"prefix-only change refused: {r.short(120)}")
    s.owner.put("/api/settings/numbering", {"doc_type": "sale", "template": cur["template"]})
    check(not bad, "; ".join(bad))
    return "preview ok, prefix change ok"


# ====================================================================== F jobs, POS, transfers

def f_1():
    """A work order refuses statuses from other pipelines (inv_paid would lock it closed)."""
    s = CTX.a
    o = s.order([labour_line(10)])
    r = s.set_status(o["id"], "inv_paid")
    now = s.get_order(o["id"])["status"]
    check(is_4xx(r) and now == "new", f"inv_paid -> {r.status}, job now {now}")
    return f"inv_paid -> {r.status}"


def f_2():
    """A coupon applied 3x at once adds one discount line."""
    s = CTX.a
    code = "GR" + uuid.uuid4().hex[:6].upper()
    must(s.owner.post("/api/coupons", {"code": code, "kind": "pct", "value": 10}), "coupon")
    o = s.order([labour_line(100)])
    res = race(RACE_N, lambda i: s.owner.post(f"/api/orders/{o['id']}/apply-coupon", {"code": code}))
    d = s.get_order(o["id"])
    n = sum(1 for x in d["lines"] if "coupon" in (x.get("description") or "").lower())
    check(n == 1, f"{n} coupon lines (want 1); total {d['total']}; {statuses(res)}")
    return f"1 coupon line, total {d['total']}"


def f_3():
    """A till return sent 3x at once refunds and restocks once."""
    s = CTX.a
    pid = s.part(qty=5)
    sd = sale(s, pid, qty=2)
    lid = sd["lines"][0]["id"]
    cash0 = s.balance(s.cash)
    res = race(RACE_N, lambda i: s.owner.post(f"/api/sales/{sd['id']}/returns",
                                              {"lines": [{"line_id": lid, "qty": 1}], "account_id": s.cash}))
    refunded = round(cash0 - s.balance(s.cash), 2)
    returned = must(s.owner.get(f"/api/sales/{sd['id']}"), "sale")["lines"][0].get("returned")
    check(s.qty(pid) == 4, f"stock {s.qty(pid)} (want 4); {statuses(res)}")
    check(refunded == 60, f"refunded {refunded} (want 60); {statuses(res)}")
    check(float(returned or 0) == 1, f"line returned count {returned} (want 1)")
    return f"once; {statuses(res)}"


def f_4():
    """Stocktake: negative count refused; lines locked after posting."""
    s = CTX.a
    pid = s.part(qty=0)
    loc, st, line = stocktake_for(s, pid, 3)
    r = s.owner.patch(f"/api/stocktake/{st}/lines/{line['id']}", {"counted_qty": -2})
    check(is_4xx(r), f"negative count accepted: {r.short()}")
    must(s.owner.patch(f"/api/stocktake/{st}/lines/{line['id']}", {"counted_qty": 3}), "count")
    must(s.owner.post(f"/api/stocktake/{st}/post"), "post")
    r = s.owner.patch(f"/api/stocktake/{st}/lines/{line['id']}", {"counted_qty": 1})
    check(is_4xx(r), f"line edited after posting: {r.short()}")
    return "negative refused, posted lines locked"


def f_5():
    """A job line refuses another shop's product (404); dropping a job puts its parts back."""
    s, b = CTX.a, CTX.b
    foreign = b.part(qty=2)
    o = s.order([labour_line(10)])
    r = s.owner.post(f"/api/orders/{o['id']}/lines", part_line(foreign))
    check(is_4xx(r), f"job line accepted another shop's product: {r.short()}")
    pid = s.part(qty=4)
    o2 = s.order([part_line(pid, qty=2)])
    must(s.set_status(o2["id"], "dropped", reason="gate", comment="gate"), "drop job")
    check(s.qty(pid) == 4, f"stock {s.qty(pid)} after dropping a job that used 2 (want 4)")
    return f"foreign product -> {r.status}; drop returned parts"


def f_6():
    """Transfers refuse negative scan qty and expose line ids; serialized stock can't rise without a serial."""
    s = CTX.a
    pid = s.part(qty=5)
    to_loc = s.location("Scan dest")
    tid = transfer_ready_to_transit(s, pid, 2, to_loc)
    sku = s.part_info(pid)["sku"]
    r = s.owner.post(f"/api/transfers/{tid}/scan", {"code": sku, "qty": -3})
    check(is_4xx(r), f"negative scan qty accepted: {r.short()}")
    t = must(s.owner.get(f"/api/transfers/{tid}"), "transfer")
    check(all(ln.get("line_id") or ln.get("id") for ln in t.get("lines", [])), "transfer lines have no line_id")
    sp = must(s.owner.post("/api/catalog/products", {"name": "Phone", "sku": "SER" + uuid.uuid4().hex[:6], "serialized": True}),
              "serialized product")
    r = s.owner.post(f"/api/inventory/parts/{sp['id']}/adjust", {"location_id": s.loc, "qty": 1})
    check(is_4xx(r), f"serialized stock rose without a serial: {r.short()}")
    return "negative scan refused, line ids present, serial required"


# ====================================================================== W3-A check-in

def checkin_point(s, kiosk=False):
    pt = must(s.owner.post("/api/checkins/points", {"name": f"Desk {uuid.uuid4().hex[:4]}", "kiosk": kiosk}), "point")
    return pt["id"], pt["path"].rsplit("/", 1)[-1]


def tiny_png():
    """A valid 1x1 PNG as a data URL (the check-in form wants a signed PNG)."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(b"\x00\x00")) + chunk(b"IEND", b""))
    return "data:image/png;base64," + base64.b64encode(png).decode()


SIGNATURE = tiny_png()


def walk_in(token, phone=None, issue="Cracked screen", **extra):
    phone = phone or "+4477009" + str(uuid.uuid4().int)[:5]
    return ANON.post(f"/api/public/checkin/{token}",
                     {"name": "Walk In", "phone": phone, "issue": issue, "agree": True, "signature": SIGNATURE,
                      **extra})


def queue(s):
    q = must(s.owner.get("/api/checkins"), "check-in queue")
    return [c for key in ("waiting", "called", "key_drops", "done") for c in q.get(key, [])]


def new_phone():
    return "+447700" + str(uuid.uuid4().int)[:6]


def find_checkin(s, phone):
    """Today's check-in for this phone (each check uses its own number)."""
    mine = [c for c in queue(s) if (c.get("phone") or "").endswith(phone[-6:])]
    check(mine, f"no check-in in today's queue for {phone}")
    return mine[0]


def w3a_1():
    """Three parallel converts of one check-in make one job."""
    s = CTX.a
    _, tok = checkin_point(s)
    phone = new_phone()
    must(walk_in(tok, phone=phone), "walk in")
    cid = find_checkin(s, phone)["id"]
    before = must(s.owner.get("/api/orders", params={"limit": 1}), "orders")["total"]
    res = race(RACE_N, lambda i: s.owner.post(f"/api/checkins/{cid}/convert"))
    after = must(s.owner.get("/api/orders", params={"limit": 1}), "orders")["total"]
    ids = {x.get("order_id") or x.get("id") for x in (r.body or {} for r in res if r.ok)}
    check(after - before == 1, f"{after - before} jobs from one check-in; {statuses(res)}")
    check(len(ids) <= 1, f"repeat converts returned different jobs {ids}")
    return f"1 job; {statuses(res)}"


def w3a_2():
    """A kiosk point takes more than 5 walk-ins from one IP in a day."""
    s = CTX.a
    _, tok = checkin_point(s, kiosk=True)
    codes = [walk_in(tok).status for _ in range(7)]
    check(all(200 <= c < 300 for c in codes), f"7 kiosk walk-ins from one IP got {codes}")
    return f"7 walk-ins -> {codes}"


def w3a_3():
    """A double-tap on the public check-in queues the customer once."""
    s = CTX.a
    _, tok = checkin_point(s)
    phone = "+4477008" + str(uuid.uuid4().int)[:5]
    res = race(2, lambda i: walk_in(tok, phone=phone))
    mine = [c for c in queue(s) if phone[-5:] in (c.get("phone") or "")]
    check(len(mine) == 1, f"{len(mine)} queue entries for one double-tap; {statuses(res)}")
    return "queued once"


def w3a_4():
    """Re-submitting a check-in keeps the original issue text."""
    s = CTX.a
    _, tok = checkin_point(s)
    phone = "+4477007" + str(uuid.uuid4().int)[:5]
    first = must(walk_in(tok, phone=phone, issue="Original issue text"), "first")
    walk_in(tok, phone=phone, issue="Second text", status_token=first.get("status_token"))
    mine = [c for c in queue(s) if phone[-5:] in (c.get("phone") or "")]
    text = " ".join(str(c.get("issue") or "") for c in mine)
    check("Original issue text" in text, f"original issue lost; queue shows {text!r}")
    return "original issue kept"


def w3a_5():
    """A cancelled check-in can't be marked no-show."""
    s = CTX.a
    _, tok = checkin_point(s)
    phone = new_phone()
    r = must(walk_in(tok, phone=phone), "walk in")
    cid = find_checkin(s, phone)["id"]
    must(ANON.post(f"/api/public/checkin/s/{r['status_token']}/cancel"), "cancel")
    r2 = s.owner.post(f"/api/checkins/{cid}/no-show")
    check(is_4xx(r2), f"cancelled check-in marked no-show: {r2.short()}")
    return f"no-show -> {r2.status}"


# ====================================================================== W3-B money reports

def report_table(s, key):
    """/api/reports/run/{key} answers {columns: [...], rows: [[...]]}: return the rows as dicts."""
    body = must(s.owner.get(f"/api/reports/run/{key}"), f"report {key}")
    cols = body.get("columns") or []
    return [dict(zip(cols, r)) if isinstance(r, list) else r for r in body.get("rows") or []]


def refund(s, oid, amount, account=None):
    """A refund on a job (goodwill / deposit) through the refunds endpoint."""
    return s.owner.post("/api/returns/refunds", {"account_id": account or s.cash, "amount": amount,
                                                 "order_id": oid, "reason": "gate check"})


def w3b_1():
    """Till return: revenue ex-tax, tax reversed, COGS of restocked units reversed."""
    s = CTX.a
    pid = s.part(qty=5, cost=10)
    b = must(s.owner.get("/api/reports/total-profit"), "tp")["buckets"]["sales"]
    sd = sale(s, pid, qty=2, price=60)
    must(s.owner.post(f"/api/sales/{sd['id']}/returns",
                      {"lines": [{"line_id": sd["lines"][0]["id"], "qty": 1}], "account_id": s.cash}), "return")
    a = must(s.owner.get("/api/reports/total-profit"), "tp")["buckets"]["sales"]
    d = {k: round(a[k] - b[k], 2) for k in ("revenue", "tax", "cogs", "profit")}
    want = {"revenue": 50.0, "tax": 10.0, "cogs": 10.0, "profit": 40.0}
    check(d == want, f"sale 2x60 incl. VAT, 1 returned+restocked: {d} (want {want})")
    return f"{d}"


def w3b_2():
    """Company dashboard refunds and cash_collected include till refunds."""
    s = CTX.a
    pid = s.part(qty=5)
    b = revenue_views(s, settle=False)
    sd = sale(s, pid, qty=1, price=60)
    must(s.owner.post(f"/api/sales/{sd['id']}/returns",
                      {"lines": [{"line_id": sd["lines"][0]["id"], "qty": 1}], "account_id": s.cash}), "return")
    a = revenue_views(s, settle=False)
    d = deltas(b, a, ["company.today.refunds", "company.today.cash_collected"])
    check(d["company.today.refunds"] == 60 and d["company.today.cash_collected"] == 0,
          f"till sale of 60 + full refund moved {d}")
    return f"{d}"


def w3b_3():
    """sold_products takes till returns off the quantity sold."""
    s = CTX.a
    pid = s.part(qty=5)
    name = s.part_info(pid)["name"]
    sd = sale(s, pid, qty=2, price=60, desc=name)
    must(s.owner.post(f"/api/sales/{sd['id']}/returns",
                      {"lines": [{"line_id": sd["lines"][0]["id"], "qty": 1}], "account_id": s.cash}), "return")
    mine = [r for r in report_table(s, "sold_products") if name in str(r.get("Product"))]
    check(mine, f"sold_products has no row for {name}")
    check(sum(float(r.get("Qty sold") or 0) for r in mine) == 1, f"sold 2, returned 1: rows {mine}")
    return "net qty 1"


def w3b_4():
    """business-insights revenue leaves out a dropped job."""
    s = CTX.a
    b = must(s.owner.get("/api/analytics/business-insights"), "insights")
    o = s.order([labour_line(500)])
    must(s.set_status(o["id"], "dropped", reason="gate", comment="gate"), "drop")
    a = must(s.owner.get("/api/analytics/business-insights"), "insights")
    rb, ra = find_num(b, "revenue"), find_num(a, "revenue")
    check(rb is not None, f"no revenue in business-insights: {str(b)[:200]}")
    check(ra == rb, f"business-insights revenue {rb} -> {ra} after a dropped 500 job")
    return f"revenue unchanged ({ra})"


def find_num(obj, key):
    """First numeric value under `key` anywhere in a JSON tree."""
    if isinstance(obj, dict):
        if isinstance(obj.get(key), (int, float)):
            return round(obj[key], 2)
        for v in obj.values():
            r = find_num(v, key)
            if r is not None:
                return r
    if isinstance(obj, list):
        for v in obj:
            r = find_num(v, key)
            if r is not None:
                return r
    return None


def cash_flow_net(s):
    return round(sum(float(r.get("Net amount") or 0) for r in report_table(s, "cash_flow")), 2)


def w3b_5():
    """cash_flow leaves out voided sales."""
    s = CTX.a
    pid = s.part(qty=5)
    before = cash_flow_net(s)
    sd = sale(s, pid, qty=1, price=33)
    mid = cash_flow_net(s)
    r = s.owner.delete(f"/api/sales/{sd['id']}")
    check(r.ok, f"void sale: {r.short()}")
    after = cash_flow_net(s)
    check(mid != before, f"cash_flow didn't move on a 33 sale ({before} -> {mid}); can't test the void")
    check(after == before, f"cash_flow net {before} -> sale {mid} -> after void {after}")
    return f"net back to {after} after void"


def w3b_6():
    """lead_sources 'Paid' takes refunds off."""
    s = CTX.a
    src = "Gate" + uuid.uuid4().hex[:4]
    cid = must(s.owner.post("/api/clients", {"name": "Lead src", "source": src}), "client")["id"]
    o = must(s.owner.post("/api/orders", {"client_id": cid, "lines": [labour_line(100)]}), "order")
    must(s.owner.post(f"/api/cash/orders/{o['id']}/pay", {"method": "cash", "account_id": s.cash}), "pay")
    must(refund(s, o["id"], 30), "refund 30")
    mine = [r for r in report_table(s, "lead_sources") if r.get("Lead source") == src]
    check(mine, f"no lead_sources row for {src}")
    check(money(mine[0].get("Paid")) == 70, f"100 paid, 30 refunded: row {mine[0]}")
    return "paid 70"


def w3b_7():
    """After a paid job is deleted, revenue doesn't go negative and receivables don't count it."""
    s = CTX.a
    b = revenue_views(s)
    o = s.order([labour_line(90)])
    s.close_paid(o["id"])
    r = s.owner.delete(f"/api/orders/{o['id']}")
    check(r.ok, f"delete paid job: {r.short()}")
    a = revenue_views(s)
    d = deltas(b, a, ["dashboard.revenue", "total_profit.revenue", "company.receivables", "dashboard.outstanding"])
    check(all(v >= 0 for v in d.values()) and d["company.receivables"] == 0 and d["dashboard.outstanding"] == 0,
          f"paid job of 90 created then deleted moved {d}")
    return f"{d}"


def w3b_8():
    """The total-profit CSV 'Refunds and other' row is not dated tomorrow."""
    s = CTX.a
    r = s.owner.get("/api/reports/total-profit.csv")
    check(r.ok, f"csv: {r.short()}")
    tomorrow = time.strftime("%Y-%m-%d", time.gmtime(time.time() + 86400 * 1.5))
    bad = [ln for ln in r.text.splitlines() if "refund" in ln.lower() and tomorrow in ln]
    check(not bad, f"rows dated {tomorrow}: {bad[:2]}")
    return "no row dated tomorrow"


def w3b_9():
    """product_turnover leaves out lines removed from a job (a kept line is the control)."""
    s = CTX.a
    removed, kept = s.part(qty=5), s.part(qty=5)
    o = s.order([part_line(removed, qty=2), part_line(kept, qty=2)])
    rid = next(x["id"] for x in o["lines"] if x["ref_id"] == removed)
    must(s.owner.delete(f"/api/orders/{o['id']}/lines/{rid}"), "remove line")
    table = report_table(s, "product_turnover")
    used = {r.get("SKU"): float(r.get("Qty used (orders/sales)") or 0) for r in table}
    sk_removed, sk_kept = s.part_info(removed)["sku"], s.part_info(kept)["sku"]
    check(used.get(sk_kept) == 2, f"control: kept line shows {used.get(sk_kept)} (want 2)")
    check(used.get(sk_removed, 0) == 0, f"removed line still counted: {used.get(sk_removed)}")
    return "removed 0, kept 2"


def w3b_10():
    """The stock value reports agree (stock_levels vs stock_valuation vs per-part stock)."""
    s = CTX.a
    levels = round(sum(float(r.get("Value (cost)") or 0) for r in report_table(s, "stock_levels")), 2)
    total = next((r for r in report_table(s, "stock_valuation") if r.get("Warehouse") == "TOTAL"), {})
    valuation = money(total.get("Stock value (cost)"))
    check(levels > 0, "no stock value at all; can't compare")
    check(levels == valuation, f"stock_levels total {levels} vs stock_valuation TOTAL {valuation}")
    return f"both {levels}"


# ====================================================================== W3-C returns, refunds, archive

def w3c_1():
    """A goods return against a job is limited to parts the job used."""
    s = CTX.a
    pid = s.part(qty=3)
    o = s.order([labour_line(20)])
    r = s.owner.post("/api/returns", {"order_id": o["id"], "lines": [{"part_id": pid, "qty": 1, "restock": True}]})
    check(is_4xx(r) and s.qty(pid) == 3, f"return of an unused part: {r.short()}; stock {s.qty(pid)} (was 3)")
    return f"refused ({r.status})"


def w3c_2():
    """Refunding a deposit on an open job leaves the job price alone."""
    s = CTX.a
    o = s.order([labour_line(300)])
    must(s.owner.post(f"/api/cash/orders/{o['id']}/prepay", {"amount": 200, "account_id": s.cash}), "deposit")
    must(refund(s, o["id"], 200), "refund deposit")
    total = s.get_order(o["id"])["total"]
    check(money(total) == 300, f"job total {total} after refunding the deposit (want 300)")
    return "total stays 300"


def w3c_3():
    """Deleting a paid job refunds each payment to its own account."""
    s = CTX.a
    o = s.order([labour_line(100)])
    must(s.owner.post(f"/api/cash/orders/{o['id']}/pay", {"method": "cash", "account_id": s.cash, "amount": 60}), "cash")
    must(s.owner.post(f"/api/cash/orders/{o['id']}/pay", {"method": "card", "account_id": s.card, "amount": 40}), "card")
    cash0, card0 = s.balance(s.cash), s.balance(s.card)
    r = s.owner.delete(f"/api/orders/{o['id']}")
    check(r.ok, f"delete job: {r.short()}")
    dc, dk = round(s.balance(s.cash) - cash0, 2), round(s.balance(s.card) - card0, 2)
    check(dc == -60 and dk == -40, f"refunds on delete: cash {dc} (want -60), card {dk} (want -40)")
    return "cash -60, card -40"


def w3c_4():
    """Goods return with restock, then deleting the job, restocks once."""
    s = CTX.a
    pid = s.part(qty=5)
    o = s.order([part_line(pid, qty=1)])
    s.close_paid(o["id"])
    must(s.owner.post("/api/returns", {"order_id": o["id"], "lines": [{"part_id": pid, "qty": 1, "restock": True}]}),
         "goods return")
    s.owner.delete(f"/api/orders/{o['id']}")
    check(s.qty(pid) == 5, f"stock {s.qty(pid)} after return + delete (want 5)")
    return "restocked once"


def w3c_5():
    """The invoice follows a refund."""
    s = CTX.a
    o = s.order([labour_line(100)])
    must(s.owner.post(f"/api/cash/orders/{o['id']}/pay", {"method": "cash", "account_id": s.cash}), "pay")
    must(refund(s, o["id"], 30), "refund 30")
    inv = must(s.owner.get(f"/api/cash/orders/{o['id']}/invoices"), "invoices")["invoices"][0]
    check(money(inv["amount_paid"]) == 70 or inv.get("status") != "paid",
          f"invoice after 100 paid, 30 refunded: {inv}")
    return f"invoice {inv.get('status')} paid {inv['amount_paid']}"


def w3c_6():
    """Deleting a posted write-off returns the qty and its cost."""
    s = CTX.a
    pid = s.part(qty=4, cost=10)
    v0 = must(s.owner.get(f"/api/inventory/parts/{pid}/stock"), "stock")["total_value"]
    doc = must(s.owner.post("/api/stock-docs/write-offs", {"location_id": s.loc, "lines": [{"part_id": pid, "qty": 2}]}),
               "write-off")
    did = doc.get("id") or doc.get("doc_id")
    must(s.owner.delete(f"/api/stock-docs/write-offs/{did}"), "delete write-off")
    st = must(s.owner.get(f"/api/inventory/parts/{pid}/stock"), "stock")
    check(st["total_available"] == 4 and money(st["total_value"]) == money(v0),
          f"after delete: qty {st['total_available']} value {st['total_value']} (want 4 / {v0})")
    return "qty and value restored"


def w3c_7():
    """Front desk with can_create_returns (no can_manage_inventory) can make a goods return."""
    s = CTX.a
    desk = s.staff_with("desk-returns", ["can_create_returns", "can_create_tickets", "can_view_client_info",
                                         "can_take_payments"])
    pid = s.part(qty=3)
    o = s.order([part_line(pid)])
    r = desk.post("/api/returns", {"order_id": o["id"], "lines": [{"part_id": pid, "qty": 1, "restock": True}]})
    check(r.ok, f"desk with can_create_returns: {r.short()}")
    return f"{r.status}"


def w3c_8():
    """Pay / prepay sent twice without an Idempotency-Key posts once."""
    s = CTX.a
    o = s.order([labour_line(100)])
    r1 = s.owner.post(f"/api/cash/orders/{o['id']}/pay", {"method": "cash", "account_id": s.cash, "amount": 10})
    r2 = s.owner.post(f"/api/cash/orders/{o['id']}/pay", {"method": "cash", "account_id": s.cash, "amount": 10})
    check(s.paid(o["id"]) == 10, f"paid {s.paid(o['id'])} after two identical pays (want 10); {r1.status}/{r2.status}")
    o2 = s.order([labour_line(100)])
    s.owner.post(f"/api/cash/orders/{o2['id']}/prepay", {"amount": 15, "account_id": s.cash})
    s.owner.post(f"/api/cash/orders/{o2['id']}/prepay", {"amount": 15, "account_id": s.cash})
    check(s.paid(o2["id"]) == 15, f"prepaid {s.paid(o2['id'])} after two identical prepays (want 15)")
    return "once each"


# ====================================================================== W3-D stock

def w3d_1():
    """Stocktake: stock used after an item is counted doesn't come back as phantom stock."""
    s = CTX.a
    pid = s.part(qty=0)
    loc, st, line = stocktake_for(s, pid, 5)
    must(s.owner.patch(f"/api/stocktake/{st}/lines/{line['id']}", {"counted_qty": 5}), "count 5")
    o = must(s.owner.post("/api/orders", {"client_id": s.client(), "location_id": loc,
                                          "lines": [part_line(pid, qty=2)]}), "job uses 2")
    check(s.qty(pid, loc) == 3, f"stock {s.qty(pid, loc)} after job (want 3) — order {o['id']}")
    must(s.owner.post(f"/api/stocktake/{st}/post"), "post")
    check(s.qty(pid, loc) == 3, f"stock {s.qty(pid, loc)} after posting a matching count (want 3)")
    return "no phantom stock"


def w3d_2():
    """Whole 'pcs' enforced on transfers and stocktake counts."""
    s = CTX.a
    pid = s.part(qty=5)
    to_loc = s.location("Half dest")
    r1 = s.owner.post("/api/transfers", {"from_location_id": s.loc, "to_location_id": to_loc,
                                         "lines": [{"part_id": pid, "qty": 1.5}]})
    pid2 = s.part(qty=0)
    _, st, line = stocktake_for(s, pid2, 3)
    r2 = s.owner.patch(f"/api/stocktake/{st}/lines/{line['id']}", {"counted_qty": 2.5})
    check(is_4xx(r1) and is_4xx(r2), f"transfer 1.5 pcs -> {r1.status}, count 2.5 pcs -> {r2.status}")
    return "both refused"


def w3d_3():
    """PO receive with qty -2 is refused (422)."""
    s = CTX.a
    pid = s.part(qty=0)
    po = must(s.owner.post("/api/inventory/po", {"supplier_id": s.supplier(), "location_id": s.loc,
                                                 "lines": [{"part_id": pid, "qty_ordered": 3, "unit_cost": 5}]}), "PO")
    po_id = po.get("id") or po.get("po_id")
    pl = must(s.owner.get(f"/api/inventory/po/{po_id}"), "PO")["lines"][0]["id"]
    r = s.owner.post(f"/api/inventory/po/{po_id}/receive", {"lines": [{"po_line_id": pl, "qty": -2}]})
    check(is_4xx(r), f"negative receive: {r.short()}")
    return f"{r.status}"


def w3d_4():
    """A draft PO that is sent/ordered leaves workflow_status po_draft."""
    s = CTX.a
    pid = s.part(qty=0)
    po = must(s.owner.post("/api/inventory/po", {"supplier_id": s.supplier(), "location_id": s.loc, "draft": True,
                                                 "lines": [{"part_id": pid, "qty_ordered": 1, "unit_cost": 5}]}), "PO")
    po_id = po.get("id") or po.get("po_id")
    r = s.owner.post(f"/api/inventory/po/{po_id}/status", {"status": "ordered"})
    if not r.ok:
        r = s.owner.post(f"/api/inventory/po/{po_id}/send")
    d = must(s.owner.get(f"/api/inventory/po/{po_id}"), "PO")
    check(d.get("workflow_status") != "po_draft", f"status {d.get('status')} but workflow_status po_draft ({r.short(100)})")
    return f"{d.get('status')} / {d.get('workflow_status')}"


def w3d_5():
    """apply-markup never lowers a sell price."""
    s = CTX.a
    pid = s.part(qty=1, cost=10, price=100)
    must(s.owner.post("/api/inventory/parts/apply-markup", {"markup_pct": 20, "part_ids": [pid]}), "markup")
    price = s.part_info(pid)["sell_price"]
    check(float(price) >= 100, f"sell price 100 -> {price} after a 20% markup on cost 10")
    return f"sell price {price}"


def w3d_6():
    """/parts/{unknown}/batches is 404."""
    r = CTX.a.owner.get(f"/api/inventory/parts/{uuid.uuid4()}/batches")
    check(r.status == 404, f"{r.short()}")
    return "404"


def w3d_7():
    """sell_price 99999999999 is a 422, not a 500."""
    r = CTX.a.owner.post("/api/inventory/parts", {"name": "Huge", "sell_price": 99999999999})
    check(is_4xx(r), f"{r.short()}")
    return f"{r.status}"


def w3d_8():
    """Another shop's warehouse access list is 404."""
    r = CTX.a.owner.get(f"/api/settings/warehouses/{CTX.b.warehouse}/access")
    check(r.status in (403, 404), f"{r.short()}")
    return f"{r.status}"


def w3d_9():
    """The reorder forecast suggests whole pcs."""
    s = CTX.a
    pid = s.part(qty=1)
    o = s.order([part_line(pid, qty=1)])
    s.close_paid(o["id"])
    fc = must(s.owner.get("/api/inventory/reorder-forecast"), "forecast")
    fractional = []
    for r in items(fc):
        for k in ("suggested_qty", "suggest_qty", "order_qty", "reorder_qty", "suggested"):
            v = r.get(k) if isinstance(r, dict) else None
            if isinstance(v, (int, float)) and v != int(v) and (r.get("uom") or "pcs") == "pcs":
                fractional.append((r.get("name"), k, v))
    check(not fractional, f"fractional pcs suggestions {fractional[:3]}")
    return f"{len(items(fc))} rows, whole numbers"


# ====================================================================== W3-E jobs, roles, auth

def w3e_1():
    """The Specialist template can't edit a delivered, paid job."""
    s = CTX.a
    r = must(s.owner.post("/api/settings/rbac/roles", {"name": f"Spec {uuid.uuid4().hex[:5]}", "template": "Specialist"}),
             "role")
    perms = role_perms(s.owner, r["id"])
    check(perms.get("can_edit_closed_ticket") is not True, "Specialist template grants can_edit_closed_ticket")
    tech = s.tech()
    o = s.order([labour_line(50)])
    s.close_paid(o["id"])
    r2 = tech.post(f"/api/orders/{o['id']}/lines", labour_line(25, "Extra"))
    check(is_4xx(r2), f"technician added a line to a delivered paid job: {r2.short()}")
    return f"line add -> {r2.status}"


def w3e_2():
    """A job line with an unknown product id is 404/422."""
    s = CTX.a
    o = s.order([labour_line(10)])
    r = s.owner.post(f"/api/orders/{o['id']}/lines", part_line(str(uuid.uuid4())))
    check(is_4xx(r), f"{r.short()}")
    return f"{r.status}"


def w3e_3():
    """A double-clicked add-line with a different description doesn't merge the lines."""
    s = CTX.a
    pid = s.part(qty=5)
    o = s.order([labour_line(10)])
    res = race(2, lambda i: s.owner.post(f"/api/orders/{o['id']}/lines", part_line(pid, desc=["Left", "Right"][i])))
    lines = [x for x in s.get_order(o["id"])["lines"] if x.get("ref_id") == pid]
    descs = sorted(x["description"] for x in lines)
    check(descs == ["Left", "Right"], f"lines after two adds with different descriptions: {descs}; {statuses(res)}")
    return "two lines"


def w3e_4():
    """POST /api/staff/roles applies the template it is sent."""
    s = CTX.a
    r = must(s.owner.post("/api/staff/roles", {"name": f"Tpl {uuid.uuid4().hex[:5]}", "template": "Specialist"}), "role")
    perms = role_perms(s.owner, r["id"])
    check(perms.get("can_create_tickets") is True, f"template ignored: {sum(1 for v in perms.values() if v)} codes on")
    return "template applied"


def w3e_5():
    """The device-limit limit_token works once."""
    s = CTX.a
    email, pw = new_email("lim"), new_password()
    must(s.owner.post("/api/staff/employees", {"name": "Limit", "email": email, "password": pw}), "employee")
    must(s.owner.put("/api/staff/company", {"max_sessions_per_login": 2}), "limit 2 devices")
    try:
        login(email, pw, "lim1")
        login(email, pw, "lim2")
        r = ANON.request("POST", "/api/auth/token", form={"username": email, "password": pw})
        check(r.status == 409, f"third device didn't hit the 2-device limit: {r.status}")
        detail = r.body.get("detail") if isinstance(r.body, dict) else None
        tok = (detail or {}).get("limit_token") if isinstance(detail, dict) else r.get("limit_token")
        sessions = (detail or {}).get("sessions") if isinstance(detail, dict) else r.get("sessions")
        check(tok and sessions, f"no limit_token in the 409 ({r.text[:120]!r})")
        sid = sessions[0].get("id") or sessions[0].get("session_id")
        a = ANON.post("/api/auth/session-limit/continue", {"limit_token": tok, "session_id": sid})
        check(a.ok, f"first continue: {a.short()}")
        b = ANON.post("/api/auth/session-limit/continue", {"limit_token": tok, "session_id": sid})
        check(not b.ok, f"limit_token replayed: {b.short(80)}")
        return f"first {a.status}, replay {b.status}"
    finally:
        s.owner.put("/api/staff/company", {"max_sessions_per_login": None})


def w3e_6():
    """An Ontario sign-up starts with HST 13%."""
    owner = register_org("on", country="CA", extra={"province": "ON", "region_code": "ON", "state": "ON"})
    taxes = items(must(owner.get("/api/catalog/taxes"), "taxes"))
    default = next((t for t in taxes if t.get("is_default")), None)
    check(default and money(default["rate"]) == 13, f"Ontario default tax {default}")
    return f"{default['name']} {default['rate']}%"


def w3e_7():
    """An AU till receipt shows the GST line and the ABN."""
    b = CTX.b
    must(b.owner.put("/api/staff/company", {"company_details": "ABN 51 824 753 556"}), "ABN")
    pid = b.part(qty=2, price=110)
    sd = sale(b, pid, qty=1, price=110)
    r = must(b.owner.get(f"/api/sales/{sd['id']}/receipt"), "receipt")
    text = str(r)
    tax = r.get("tax")
    check(tax and float(tax) > 0, f"AU receipt tax {tax} on a 110 sale with GST included")
    check("GST" in text, "AU receipt has no GST line")
    check("ABN" in text or "51 824 753 556" in text, "AU receipt has no ABN")
    return f"GST {tax}"


# ====================================================================== registry

CHECKS = [
    ("QA-1", "double accept/convert estimate", qa_1),
    ("QA-2", "approved recommendation takes stock + cost", qa_2),
    ("QA-3", "double decide recommendations", qa_3),
    ("QA-4", "double pay / prepay with key", qa_4),
    ("QA-5", "transfer ready/completed x3", qa_5),
    ("QA-6", "stocktake posted x3", qa_6),
    ("QA-7", "two staff edit one line", qa_7),
    ("QA-8", "same line removed x3", qa_8),
    ("QA-9", "one job, one invoice", qa_9),
    ("QA-10", "new role gets only sent permissions", qa_10),
    ("QA-11", "new default tax charged on jobs", qa_11),
    ("QA-12", "portal on delivered job / declined estimate", qa_12),
    ("QA-13", "counter return sent twice", qa_13),
    ("QA-14", "concurrent PO receive / duplicate SKU", qa_14),
    ("QA-15", "work order refuses foreign statuses", qa_15),
    ("A-1", "staff/roles partial permission map", a_1),
    ("A-2", "technician money visibility", a_2),
    ("A-3", "technician close unpaid / cost edit", a_3),
    ("B-1", "default tax moves the rules stack", b_1),
    ("B-2", "coupon >100% / coupon before tax", b_2),
    ("B-3", "tax + pricing validation", b_3),
    ("B-4", "service + commission validation", b_4),
    ("B-5", "invoice-preview line discounts", b_5),
    ("C-1", "one revenue figure across screens", c_1),
    ("C-2", "dashboard revenue after payment", c_2),
    ("C-3", "total-profit till sales ex-tax, returns", c_3),
    ("C-4", "receivables agree", c_4),
    ("D-1", "opening stock cost", d_1),
    ("D-2", "archive part with stock", d_2),
    ("D-3", "write-off over stock / sign", d_3),
    ("D-4", "free PO line cost", d_4),
    ("D-5", "movements carry a warehouse", d_5),
    ("E-1", "unknown ids are 4xx", e_1),
    ("E-2", "duplicate phone check", e_2),
    ("E-3", "company currency / timezone validation", e_3),
    ("E-4", "numbering templates", e_4),
    ("F-1", "work order status pipeline", f_1),
    ("F-2", "coupon applied x3", f_2),
    ("F-3", "till return x3", f_3),
    ("F-4", "stocktake negative / locked after post", f_4),
    ("F-5", "foreign product on job / drop returns parts", f_5),
    ("F-6", "transfer scan / serial guard", f_6),
    ("W3-A-1", "check-in convert x3", w3a_1),
    ("W3-A-2", "kiosk walk-in limit", w3a_2),
    ("W3-A-3", "public check-in double-tap", w3a_3),
    ("W3-A-4", "check-in re-submit keeps issue", w3a_4),
    ("W3-A-5", "cancelled check-in no-show", w3a_5),
    ("W3-B-1", "till return in total profit", w3b_1),
    ("W3-B-2", "company dashboard till refunds", w3b_2),
    ("W3-B-3", "sold_products net of returns", w3b_3),
    ("W3-B-4", "insights without dropped job", w3b_4),
    ("W3-B-5", "cash_flow without voided sale", w3b_5),
    ("W3-B-6", "lead_sources paid minus refunds", w3b_6),
    ("W3-B-7", "deleted paid job in reports", wave("W3-C", w3b_7)),
    ("W3-B-8", "CSV refunds row date", w3b_8),
    ("W3-B-9", "product_turnover removed lines", w3b_9),
    ("W3-B-10", "stock value reports agree", w3b_10),
    ("W3-C-1", "return limited to used parts", wave("W3-C", w3c_1)),
    ("W3-C-2", "deposit refund keeps price", wave("W3-C", w3c_2)),
    ("W3-C-3", "delete paid job refunds per account", wave("W3-C", w3c_3)),
    ("W3-C-4", "return + delete restocks once", wave("W3-C", w3c_4)),
    ("W3-C-5", "invoice follows refund", wave("W3-C", w3c_5)),
    ("W3-C-6", "deleting write-off restores cost", wave("W3-C", w3c_6)),
    ("W3-C-7", "desk can_create_returns", wave("W3-C", w3c_7)),
    ("W3-C-8", "pay/prepay without key twice", wave("W3-C", w3c_8)),
    ("W3-D-1", "stocktake phantom stock", w3d_1),
    ("W3-D-2", "whole pcs on transfer/count", w3d_2),
    ("W3-D-3", "PO receive negative qty", w3d_3),
    ("W3-D-4", "PO workflow leaves po_draft", w3d_4),
    ("W3-D-5", "apply-markup never lowers", w3d_5),
    ("W3-D-6", "unknown part batches 404", w3d_6),
    ("W3-D-7", "huge sell_price 422", w3d_7),
    ("W3-D-8", "foreign warehouse access 404", w3d_8),
    ("W3-D-9", "reorder forecast whole pcs", w3d_9),
    ("W3-E-1", "Specialist can't edit delivered job", wave("W3-E", w3e_1)),
    ("W3-E-2", "job line unknown product id", wave("W3-E", w3e_2)),
    ("W3-E-3", "double add-line keeps descriptions", wave("W3-E", w3e_3)),
    ("W3-E-4", "staff/roles applies template", wave("W3-E", w3e_4)),
    ("W3-E-5", "limit_token single use", wave("W3-E", w3e_5)),
    ("W3-E-6", "Ontario sign-up HST 13%", wave("W3-E", w3e_6)),
    ("W3-E-7", "AU receipt GST + ABN", wave("W3-E", w3e_7)),
]


def main():
    log(f"Tekly regression gate — {len(CHECKS)} checks")
    return run_checks(CHECKS, parse_only(sys.argv))


if __name__ == "__main__":
    sys.exit(main())
