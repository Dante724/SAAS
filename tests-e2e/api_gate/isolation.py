"""ISO-1 tenant-isolation sweep.

Two fresh shops. Shop A gets one real record of every kind we can create. Then, for every
route in /openapi.json with an {id} in its path, shop B's owner calls it with A's real ids:
GET first, then POST/PUT/PATCH, then DELETE (so a delete can't hide a later probe).

    python tests-e2e/api_gate/isolation.py          (TEKLY_URL=... for another server)

Per probe:
  blocked   404 / 403 / 401                          -> pass
  leak      2xx whose body holds A's data, or A's data changed afterwards
  2xx-empty 2xx without any of A's data (still not the 404 we want; listed, counted as a fail)
  other     400/405/409/422 (refused before or without the lookup; listed, not a leak)
  5xx       server error (listed, counted as a fail)

Afterwards every seeded A record is read back as A and compared with the snapshot taken
before the sweep. Exit 1 on any leak, 2xx, 5xx or changed A data.
"""
import json
import sys
import uuid

import regress
from gate import Api, items, log, must, register_org

# Not tenant-scoped by a signed-in user: public token pages, provider webhooks, platform admin,
# SSO redirects. They have their own checks (portal tokens, signatures, admin login).
EXCLUDE_PREFIXES = ("/api/public/", "/api/admin/", "/api/sso/authorize/", "/api/payments/stripe/webhook",
                    "/api/payments/square/webhook", "/api/chats/webhook", "/api/inbox/webhook",
                    "/api/comms/calls/webhook")
METHOD_ORDER = {"get": 0, "post": 1, "put": 1, "patch": 1, "delete": 2}
VOLATILE = ("minutes_in_status", "waited_min", "status_sla_overdue", "overdue_by_date", "days_left",
            "last_seen", "updated_at", "age_days", "days_open",
            "duration_min", "duration_sec")   # a running timer keeps counting during the sweep


# ---------------------------------------------------------------- seed shop A

def seed(shop):
    """Create one record of each kind in shop A. Returns {param_key: id} and the A-only markers."""
    o = shop.owner
    ids = {}
    pid = shop.part(qty=5)
    ids["part"] = pid
    ids["client"] = shop.client()
    ids["supplier"] = shop.supplier()
    order = shop.order([regress.part_line(pid, qty=1), regress.labour_line(40)])
    ids["order"] = order["id"]
    ids["line"] = order["lines"][0]["id"]
    item = o.post(f"/api/orders/{order['id']}/items", {"label": "Gate phone", "complaint": "x"})
    if item.ok:
        ids["order_item"] = item.get("id")
    rec = o.post(f"/api/orders/{order['id']}/recommendations", {"title": "Gate rec", "unit_price": 5})
    ids["rec"] = rec.get("id")
    est = shop.order([regress.labour_line(30)], estimate=True)
    ids["estimate"] = est["id"]
    must(o.post(f"/api/cash/orders/{order['id']}/pay", {"method": "cash", "account_id": shop.cash, "amount": 10}),
         "pay")
    invs = must(o.get(f"/api/cash/orders/{order['id']}/invoices"), "invoices")
    ids["invoice"] = invs["invoices"][0]["id"] if invs.get("invoices") else None
    txns = items(o.get("/api/cash/transactions", params={"limit": 5}).body)
    ids["txn"] = txns[0]["id"] if txns else None
    ids["account"] = shop.cash
    ids["location"] = shop.loc
    ids["warehouse"] = shop.warehouse
    to_loc = shop.location("Iso dest")
    t = must(o.post("/api/transfers", {"from_location_id": shop.loc, "to_location_id": to_loc,
                                       "lines": [{"part_id": pid, "qty": 1}]}), "transfer")
    ids["transfer"] = t["id"]
    st = must(o.post("/api/stocktake", {"location_id": to_loc}), "stocktake")
    ids["stocktake"] = st["id"]
    po = must(o.post("/api/inventory/po", {"supplier_id": ids["supplier"], "location_id": shop.loc,
                                           "lines": [{"part_id": pid, "qty_ordered": 1, "unit_cost": 3}]}), "PO")
    ids["po"] = po.get("id") or po.get("po_id")
    sd = regress.sale(shop, pid, qty=1, price=20)
    ids["sale"] = sd["id"]
    wo = o.post("/api/stock-docs/write-offs", {"location_id": shop.loc, "lines": [{"part_id": pid, "qty": 1}]})
    ids["writeoff"] = wo.get("id") or wo.get("doc_id")
    ret = o.post("/api/returns", {"order_id": order["id"], "lines": [{"part_id": pid, "qty": 1, "restock": True}]})
    ids["return"] = ret.get("id") or ret.get("return_id")
    ids["tax"] = next(t["id"] for t in items(must(o.get("/api/catalog/taxes"), "taxes")))
    ids["service"] = must(o.post("/api/catalog/services", {"name": "Gate iso svc", "price": 10}), "service")["id"]
    ids["category"] = must(o.post("/api/catalog/categories", {"name": "Gate iso cat"}), "category")["id"]
    ids["role"] = must(o.post("/api/settings/rbac/roles", {"name": "Gate iso role",
                                                           "permissions": {"can_create_tickets": True}}), "role")["id"]
    emp = must(o.post("/api/staff/employees", {"name": "Gate iso staff", "email": f"iso-{uuid.uuid4().hex[:6]}@tekly.test",
                                               "role_id": ids["role"]}), "employee")
    ids["employee"] = emp.get("id")
    inv = o.post("/api/staff/invites", {"email": f"iso-inv-{uuid.uuid4().hex[:6]}@tekly.test"})
    ids["invite"] = inv.get("id")
    pt = must(o.post("/api/checkins/points", {"name": "Gate iso point"}), "point")
    ids["point"] = pt["id"]
    regress.walk_in(pt["path"].rsplit("/", 1)[-1], phone="+447700" + str(uuid.uuid4().int)[:6])
    q = regress.queue(shop)
    ids["checkin"] = q[0]["id"] if q else None
    ids["lead"] = o.post("/api/leads", {"name": "Gate iso lead", "phone": "+447700900555"}).get("id")
    ids["appointment"] = o.post("/api/appointments", {"client_id": ids["client"],
                                                      "scheduled_for": "2030-01-02T10:00:00"}).get("id")
    ids["task"] = o.post("/api/tasks", {"title": "Gate iso task"}).get("id")
    ids["tag"] = o.post("/api/tags", {"name": "Gate iso tag"}).get("id")
    ids["resource"] = o.post("/api/resources", {"name": "Gate iso bay"}).get("id")
    ids["bin"] = o.post("/api/bins", {"location_id": shop.loc, "name": "Gate iso bin"}).get("id")
    ids["status"] = next((s["id"] for s in items(o.get("/api/statuses").body)), None)
    ids["org"] = o.me["org_id"]
    # Less central records: a failed create just leaves that kind unprobed (counted as skipped).
    optional = {
        "asset": ("/api/assets", {"client_id": ids["client"], "brand": "Gate", "model": "Iso"}),
        "thread": ("/api/chats/threads", {"contact_phone": "+447700900556", "contact_name": "Gate"}),
        "directory": ("/api/directories", {"key": "gate_iso_" + uuid.uuid4().hex[:4], "name": "Gate iso"}),
        "webhook": ("/api/webhooks", {"url": "https://example.com/gate-hook", "events": ["order.created"]}),
        "checklist_template": ("/api/checklist-templates", {"name": "Gate iso list", "items": [{"label": "Check"}]}),
        "custom_report": ("/api/custom-reports", {"name": "Gate iso report", "config": {"source": "orders"}}),
        "bundle": ("/api/bundles", {"name": "Gate iso kit", "components": [{"ref_type": "product", "ref_id": pid}]}),
        "legal_entity": ("/api/settings/legal-entities", {"name": "Gate iso Ltd"}),
        "print_template": ("/api/print/templates", {"name": "Gate iso label"}),
        "doc_template": ("/api/documents/templates", {"name": "Gate iso doc", "body_html": "<p>x</p>"}),
        "api_key": ("/api/api-keys", {"name": "Gate iso key"}),
        "saved_filter": ("/api/orders/filters/saved", {"name": "Gate iso filter", "params": {"status": "new"}}),
        "lead_type": ("/api/leads/types", {"name": "Gate iso lead type"}),
        "pricing_type": ("/api/pricing/types", {"name": "Gate iso price"}),
        "form_type": ("/api/forms/types", {"name": "Gate iso job type"}),
        "credit_note": ("/api/finance/credit-notes", {"client_id": ids["client"],
                                                      "lines": [{"description": "x", "qty": 1, "unit_price": 1}]}),
        "notif_template": ("/api/notifications/templates", {"body_template": "Gate {order_number}"}),
    }
    for key, (path, body) in optional.items():
        r = o.post(path, body)
        if r.ok:
            ids[key] = r.get("id")
    if ids.get("checklist_template"):
        r = o.post(f"/api/orders/{order['id']}/checklists", {"template_id": ids["checklist_template"]})
        ids["order_checklist"] = r.get("id") if r.ok else None
    r = o.post("/api/timeclock/start", {"order_id": order["id"]})
    ids["time_entry"] = (r.get("id") or (r.get("entry") or {}).get("id")) if r.ok else None
    return {k: v for k, v in ids.items() if v}


# Path parameter -> seeded record. Ambiguous names are decided by the segment before them.
BY_SEGMENT = {
    ("transfers", "{tid}"): "transfer", ("tags", "{tid}"): "tag", ("points", "{pid}"): "point",
    ("products", "{pid}"): "part", ("checkins", "{cid}"): "checkin", ("categories", "{cid}"): "category",
    ("contacts", "{cid}"): "client", ("inquiries", "{lid}"): "lead", ("locations", "{lid}"): "location",
    ("employees", "{eid}"): "employee", ("estimates", "{eid}"): "estimate", ("invoices", "{iid}"): "invoice",
    ("services", "{sid}"): "service", ("appointments", "{bid}"): "appointment", ("appointments", "{aid}"): "appointment",
    ("write-offs", "{doc_id}"): "writeoff", ("returns", "{doc_id}"): "return", ("returns", "{return_id}"): "return",
    ("items", "{item_id}"): "order_item", ("resources", "{rid}"): "resource", ("orders", "{oid}"): "order",
    ("products", "{unit_id}"): "part",
}
# Same segment and name under different parents (e.g. /forms/types/{type_id} vs /leads/types/{type_id}).
BY_TWO_SEGMENTS = {
    ("forms", "types", "{type_id}"): "form_type", ("leads", "types", "{type_id}"): "lead_type",
    ("pricing", "types", "{type_id}"): "pricing_type", ("chats", "threads", "{tid}"): "thread",
    ("documents", "templates", "{tid}"): "doc_template", ("print", "templates", "{tpl_id}"): "print_template",
    ("timeclock", "entries", "{eid}"): "time_entry", ("finance", "credit-notes", "{cn_id}"): "credit_note",
    ("api", "webhooks", "{eid}"): "webhook", ("api", "bundles", "{bundle_id}"): "bundle",
    ("v2", "bundles", "{bid}"): "bundle", ("api", "checklist-templates", "{tid}"): "checklist_template",
    ("api", "custom-reports", "{rid}"): "custom_report", ("api", "directories", "{directory_id}"): "directory",
    ("settings", "legal-entities", "{entity_id}"): "legal_entity", ("api", "api-keys", "{key_id}"): "api_key",
    ("saved", "{filter_id}"): "saved_filter", ("notifications", "templates", "{template_id}"): "notif_template",
    ("api", "assets", "{asset_id}"): "asset", ("{order_id}", "checklists", "{cid}"): "order_checklist",
}
BY_PARAM = {
    "{order_id}": "order", "{client_id}": "client", "{part_id}": "part", "{po_id}": "po", "{st_id}": "stocktake",
    "{sale_id}": "sale", "{supplier_id}": "supplier", "{tax_id}": "tax", "{service_id}": "service",
    "{account_id}": "account", "{location_id}": "location", "{warehouse_id}": "warehouse", "{role_id}": "role",
    "{emp_id}": "employee", "{employee_id}": "employee", "{invite_id}": "invite", "{lead_id}": "lead",
    "{task_id}": "task", "{tag_id}": "tag", "{invoice_id}": "invoice", "{txn_id}": "txn", "{line_id}": "line",
    "{rec_id}": "rec", "{bin_id}": "bin", "{status_id}": "status", "{org_id}": "org",
}


def fill_path(path, ids):
    """Replace every {param} with A's id. Returns (url, all_real) — all_real False when some param had no seed."""
    segs = path.split("/")
    real = True
    for i, s in enumerate(segs):
        if s.startswith("{"):
            key = (BY_TWO_SEGMENTS.get((segs[i - 2], segs[i - 1], s)) or BY_TWO_SEGMENTS.get((segs[i - 1], s))
                   or BY_SEGMENT.get((segs[i - 1], s)) or BY_PARAM.get(s))
            if key and ids.get(key):
                segs[i] = ids[key]
            else:
                segs[i] = str(uuid.uuid4())
                real = False
    return "/".join(segs), real


# ---------------------------------------------------------------- request bodies from the schema

def example(schema, comps, depth=0):
    if depth > 4:
        return None
    if "$ref" in schema:
        return example(comps[schema["$ref"].split("/")[-1]], comps, depth + 1)
    if "anyOf" in schema:
        return example(next((s for s in schema["anyOf"] if s.get("type") != "null"), {}), comps, depth + 1)
    t = schema.get("type")
    if "enum" in schema:
        return schema["enum"][0]
    if t == "object" or "properties" in schema:
        req = schema.get("required", [])
        return {k: example(v, comps, depth + 1) for k, v in schema.get("properties", {}).items() if k in req}
    if t == "array":
        return []
    if t == "integer":
        return 1
    if t == "number":
        return 1.0
    if t == "boolean":
        return False
    return "gate"


def body_for(op, comps):
    content = (op.get("requestBody") or {}).get("content") or {}
    if "application/json" in content:
        return example(content["application/json"].get("schema", {}), comps)
    return None


# ---------------------------------------------------------------- snapshot of A

def snapshot_targets(ids):
    t = {
        "order": f"/api/orders/{ids['order']}", "estimate": f"/api/orders/{ids['estimate']}",
        "client": f"/api/clients/{ids['client']}", "part": f"/api/inventory/parts/{ids['part']}",
        "stock": f"/api/inventory/parts/{ids['part']}/stock", "po": f"/api/inventory/po/{ids['po']}",
        "transfer": f"/api/transfers/{ids['transfer']}", "stocktake": f"/api/stocktake/{ids['stocktake']}/lines",
        "sale": f"/api/sales/{ids['sale']}", "accounts": "/api/cash/accounts", "taxes": "/api/catalog/taxes",
        "locations": "/api/staff/locations", "warehouses": "/api/settings/warehouses",
        "roles": "/api/settings/rbac/roles", "employees": "/api/staff/employees", "company": "/api/staff/company",
        "services": "/api/catalog/services", "categories": "/api/catalog/categories", "suppliers": "/api/suppliers",
        "statuses": "/api/statuses", "tasks": "/api/tasks", "tags": "/api/tags", "leads": "/api/leads",
        "order_payments": f"/api/cash/orders/{ids['order']}/invoices",
        "resources": "/api/resources", "bins": "/api/bins", "appointments": "/api/appointments",
        "webhooks": "/api/webhooks", "bundles": "/api/bundles", "api_keys": "/api/api-keys",
        "checklist_templates": "/api/checklist-templates", "custom_reports": "/api/custom-reports",
        "legal_entities": "/api/settings/legal-entities", "doc_templates": "/api/documents/templates",
        "print_templates": "/api/print/templates", "directories": "/api/directories",
        "credit_notes": "/api/finance/credit-notes", "points": "/api/checkins/points",
        "lead_types": "/api/leads/types", "pricing_types": "/api/pricing/types", "form_types": "/api/forms/types",
        "assets": "/api/assets", "threads": "/api/chats/threads", "saved_filters": "/api/orders/filters/saved",
        "checkins": "/api/checkins", "invites": "/api/staff/invites", "notif_templates": "/api/notifications/templates",
        "transactions": "/api/cash/transactions", "write_offs": "/api/stock-docs/write-offs",
        "returns": "/api/stock-docs/returns", "time_entries": "/api/timeclock/entries",
    }
    if ids.get("invoice"):
        t["invoice"] = f"/api/finance/invoices/{ids['invoice']}"
    return t


def strip(obj):
    if isinstance(obj, dict):
        return {k: strip(v) for k, v in obj.items() if k not in VOLATILE}
    if isinstance(obj, list):
        return [strip(v) for v in obj]
    return obj


def snapshot(owner, targets):
    out = {}
    for name, path in targets.items():
        r = owner.get(path)
        out[name] = (r.status, json.dumps(strip(r.body), sort_keys=True, default=str))
    return out


# ---------------------------------------------------------------- sweep

def main():
    log("ISO-1 isolation sweep")
    spec = must(Api().get("/openapi.json"), "openapi")
    comps = spec.get("components", {}).get("schemas", {})
    log("  (signing up shop A and shop B)")
    shop_a = regress.Shop(register_org("iso-a", country="GB"))
    shop_b = regress.Shop(register_org("iso-b", country="US"))
    ids = seed(shop_a)
    log(f"  seeded {len(ids)} kinds of A records")
    markers = {v for k, v in ids.items() if k != "org"} | {shop_a.owner.org_name}
    targets = snapshot_targets(ids)
    before = snapshot(shop_a.owner, targets)

    ops = []
    for path, methods in spec["paths"].items():
        if "{" not in path or path.startswith(EXCLUDE_PREFIXES):
            continue
        for m, op in methods.items():
            if m in METHOD_ORDER:
                ops.append((METHOD_ORDER[m], path, m, op))
    ops.sort(key=lambda x: (x[0], x[1]))

    counts = {"blocked": 0, "leak": 0, "2xx-empty": 0, "other": 0, "5xx": 0}
    findings = {"leak": [], "2xx-empty": [], "5xx": [], "other": []}
    real_probes = 0
    unseeded = 0
    written = []      # 2xx writes: their A records must still be there, unchanged
    b = shop_b.owner
    for _, path, m, op in ops:
        url, real = fill_path(path, ids)
        if not real:
            unseeded += 1
            continue
        real_probes += 1
        body = body_for(op, comps) if m in ("post", "put", "patch") else None
        if m in ("post", "put", "patch") and body is None:
            body = {}
        r = b.request(m.upper(), url, json_body=body)
        if r.status in (401, 403, 404):
            counts["blocked"] += 1
            continue
        if 200 <= r.status < 300:
            text = r.text or ""
            # A's ids that appear in the answer, other than the ones we put in the URL ourselves.
            hit = [k for k in markers if k and k in text and k not in url]
            kind = "leak" if hit else "2xx-empty"
            counts[kind] += 1
            findings[kind].append(f"{m.upper()} {path} -> {r.status} {text[:140]!r}" + (f" [A data: {hit[:2]}]" if hit else ""))
            if m != "get":
                written.append((m.upper(), path, [v for v in ids.values() if v in url]))
        elif r.status >= 500:
            counts["5xx"] += 1
            findings["5xx"].append(f"{m.upper()} {path} -> {r.status} {r.text[:120]!r}")
        else:
            counts["other"] += 1
            findings["other"].append(f"{m.upper()} {path} -> {r.status} {r.text[:90]!r}")

    after = snapshot(shop_a.owner, targets)
    changed = [name for name in targets if before[name] != after[name]]
    before_text = " ".join(v[1] for v in before.values())
    after_text = " ".join(v[1] for v in after.values())
    covered = sorted(k for k, v in ids.items() if v in before_text)
    # A 2xx write on A's id: the record must still be in A's snapshot (or we can't vouch for it).
    gone, unverified = [], []
    for method, path, used in written:
        for rid in used:
            if rid not in before_text:
                unverified.append(f"{method} {path}")
            elif rid not in after_text:
                gone.append(f"{method} {path} removed A's record {rid}")

    log("")
    for kind in ("leak", "2xx-empty", "5xx"):
        for f in findings[kind]:
            log(f"{kind.upper():9} {f}")
    if findings["other"]:
        log(f"\n{len(findings['other'])} probes refused with 400/405/409/422 before the lookup (not leaks), e.g.:")
        for f in findings["other"][:8]:
            log(f"  {f}")
    for name in changed:
        log(f"CHANGED   A's {name} differs after the sweep ({targets[name]})")
        log(f"   before {before[name][1][:300]}")
        log(f"   after  {after[name][1][:300]}")
    for g in gone:
        log(f"GONE      {g}")
    for u in sorted(set(unverified)):
        log(f"UNVERIFIED 2xx write {u}: A's record isn't in the snapshot, so 'unchanged' isn't proven")
    log(f"\nsnapshot covers {len(covered)} of {len(ids)} seeded A records")
    leaks = counts["leak"] + len(changed) + len(gone)
    log("")
    log(f"SUMMARY ISO-1: {real_probes} probes with A's real ids over {len(ops)} id-routes "
        f"({unseeded} routes skipped: no A record of that kind) — blocked {counts['blocked']}, "
        f"leaks {leaks} (body {counts['leak']}, A data changed {len(changed)}), "
        f"2xx-without-A-data {counts['2xx-empty']}, 5xx {counts['5xx']}, refused-before-lookup {counts['other']}")
    return 1 if (leaks or counts["2xx-empty"] or counts["5xx"]) else 0


if __name__ == "__main__":
    sys.exit(main())
