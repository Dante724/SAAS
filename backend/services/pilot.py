"""Tekly Go shop pilot (docs/pilot/tekly-go-shop-pilot.md).

The question: do bench techs pull out their phone to update jobs, or is Go a counter-and-owner
app? Three pieces answer it:

- QR job labels taped on tickets, each a link to the job in Go (`render_job_labels`).
- A record of where each counted action came from. Status changes and customer messages carry
  the request's `client` column (core/client_source); photos added and job lookups (scans and
  searches) are audit_log rows, which carry it too. Searches are logged by kind (name, phone,
  ticket number, last 4 digits), never by what was typed.
- A report per employee over a date range and a set of jobs (`go_report`), with the headline
  share of status changes made on a phone against the 50% decision rule.
"""
import io
import re
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy import select

from backend.core.client_source import is_go
from backend.models import AuditLog, Employee, Order, OrderComment, OrderStatusLog

LOOKUP = "pilot.lookup"            # diff: {via: search|scan, by: <kind>, surface, matched?}
PHOTOS = "order.photos_added"      # diff: {count, how}
LABELS = "pilot.labels_printed"    # one row per job that got a label; defines the pilot's jobs

DECISION_THRESHOLD = 0.5           # share of status changes on the phone (owner's decision rule)
SEARCH_KINDS = ("name", "phone", "wo", "last4", "email", "other")
COLLAPSE_SECONDS = 20              # one typing burst in a search box is one lookup


# ── lookups ──────────────────────────────────────────────────────────────────
_WO = re.compile(r"(?:[A-Za-z]{1,5}[-\s]?|#)\d{1,9}")


def classify_query(q: Optional[str]) -> str:
    """What kind of thing someone typed to find a job. The text itself is never stored.

    Four digits on their own count as the last 4 of a phone number (how counters usually ask);
    other short numbers are a ticket number typed without its prefix.
    """
    s = (q or "").strip()
    if not s:
        return "other"
    if "@" in s:
        return "email"
    if _WO.fullmatch(s):
        return "wo"
    digits = re.sub(r"[\s\-().+]", "", s)
    if digits.isdigit():
        if len(digits) == 4:
            return "last4"
        return "phone" if len(digits) >= 6 else "wo"
    if re.search(r"[^\W\d_]", s):
        return "name"
    return "other"


async def record_lookup(db, current: Employee, *, via: str, by: str, surface: str,
                        order_id: Optional[str] = None, matched: Optional[bool] = None,
                        collapse: bool = False) -> None:
    """Log one job lookup and commit. Best effort: a failure here never breaks the lookup.

    With `collapse`, a lookup by the same person on the same surface within COLLAPSE_SECONDS
    replaces the previous one, so "M", "Ma", "Mar…" while typing counts once, as its last kind.
    """
    diff = {"via": via, "by": by, "surface": surface}
    if matched is not None:
        diff["matched"] = bool(matched)
    try:
        now = datetime.utcnow()
        if collapse:
            prev = (await db.execute(select(AuditLog).where(
                AuditLog.org_id == current.org_id, AuditLog.actor_id == current.id,
                AuditLog.action == LOOKUP,
                AuditLog.created_at >= now - timedelta(seconds=COLLAPSE_SECONDS),
            ).order_by(AuditLog.created_at.desc()).limit(1))).scalars().first()
            if prev is not None and (prev.diff or {}).get("surface") == surface:
                prev.diff, prev.created_at, prev.entity_id = diff, now, order_id
                await db.commit()
                return
        db.add(AuditLog(org_id=current.org_id, actor_id=current.id, action=LOOKUP,
                        entity_type="order", entity_id=order_id, diff=diff, created_at=now))
        await db.commit()
    except Exception:  # noqa: BLE001 — measurement must never cost the user their answer
        await db.rollback()


def record_photos(db, current: Employee, order_id: str, count: int, how: str) -> None:
    """Note photos added to a job (caller commits, so it shares the business transaction)."""
    if count > 0:
        db.add(AuditLog(org_id=current.org_id, actor_id=current.id, action=PHOTOS,
                        entity_type="order", entity_id=order_id, diff={"count": count, "how": how}))


# ── QR job labels ────────────────────────────────────────────────────────────
LABEL_W_MM, LABEL_H_MM = 64, 38
PAPERS = ("a4", "letter")


def first_name(full: Optional[str]) -> str:
    return (full or "").strip().split(" ")[0][:20] if full else ""


def device_text(order: Order) -> str:
    d = getattr(order, "device", None)
    if d is not None:
        txt = " ".join(x for x in [d.brand, d.model] if x)
        if txt:
            return txt
    return ""


def job_link(base: str, order_id: str) -> str:
    """The label's QR: opens the job in Tekly Go. `via=qr` lets Go count the scan when the phone's
    own camera opened the link."""
    return f"{base.rstrip('/')}/go/job/{order_id}?via=qr"


def _draw_qr(c, data: str, x: float, y: float, size: float) -> None:
    import segno
    rows = [list(r) for r in segno.make(data, error="m").matrix]
    n = len(rows)
    m = size / (n + 4)                       # a 2-module quiet zone on every side
    c.setFillColorRGB(0, 0, 0)
    p = c.beginPath()
    for r, row in enumerate(rows):
        col = 0
        while col < n:
            if not row[col]:
                col += 1
                continue
            run = col
            while run < n and row[run]:
                run += 1
            # one rectangle per dark run, overlapping the next row a hair, so the printed QR has
            # no anti-aliased seams between modules
            p.rect(x + (col + 2) * m, y + size - (r + 3) * m - 0.02, (run - col) * m, m + 0.04)
            col = run
    c.drawPath(p, stroke=0, fill=1)


def _fit(c, text: str, font: str, size: float, max_w: float) -> str:
    if c.stringWidth(text, font, size) <= max_w:
        return text
    while text and c.stringWidth(text + "…", font, size) > max_w:
        text = text[:-1]
    return text + "…"


def _wrap(c, text: str, font: str, size: float, max_w: float, lines: int) -> list[str]:
    out, cur = [], ""
    for word in text.split():
        nxt = f"{cur} {word}".strip()
        if c.stringWidth(nxt, font, size) <= max_w or not cur:
            cur = nxt
        else:
            out.append(cur)
            cur = word
    if cur:
        out.append(cur)
    if len(out) > lines:
        out = out[:lines - 1] + [" ".join(out[lines - 1:])]
    return [_fit(c, x, font, size, max_w) for x in out]


def render_job_labels(labels: list[dict], *, paper: str = "a4", org_name: str = "") -> bytes:
    """A sheet of 64×38mm job labels: QR on the left; WO number, first name and device on the
    right. Each item: {url, number, name, device}."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, letter
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas as pdfcanvas

    size = letter if paper == "letter" else A4
    buf = io.BytesIO()
    c = pdfcanvas.Canvas(buf, pagesize=size)
    c.setTitle("Tekly Go job labels")
    page_w, page_h = size
    lw, lh, margin = LABEL_W_MM * mm, LABEL_H_MM * mm, 8 * mm
    cols = max(1, int((page_w - 2 * margin) // lw))
    rows = max(1, int((page_h - 2 * margin) // lh))
    per_page = cols * rows
    for i, it in enumerate(labels):
        slot = i % per_page
        if i and slot == 0:
            c.showPage()
        x = margin + (slot % cols) * lw
        y = page_h - margin - (slot // cols + 1) * lh
        c.setStrokeColor(colors.HexColor("#DDDDDD"))
        c.rect(x, y, lw, lh, stroke=1, fill=0)
        qr = lh - 4 * mm
        _draw_qr(c, it["url"], x + 2 * mm, y + 2 * mm, qr)
        tx = x + qr + 3 * mm
        tw = lw - qr - 5 * mm
        c.setFillColorRGB(0, 0, 0)
        c.setFont("Courier-Bold", 11)
        c.drawString(tx, y + lh - 9 * mm, _fit(c, it.get("number") or "", "Courier-Bold", 11, tw))
        c.setFont("Helvetica-Bold", 10)
        c.drawString(tx, y + lh - 15 * mm, _fit(c, it.get("name") or "", "Helvetica-Bold", 10, tw))
        c.setFont("Helvetica", 8)
        for k, line in enumerate(_wrap(c, it.get("device") or "", "Helvetica", 8, tw, 2)):
            c.drawString(tx, y + lh - (20 + 3.6 * k) * mm, line)
        c.setFont("Helvetica", 6)
        c.setFillColor(colors.HexColor("#555555"))
        c.drawString(tx, y + 4 * mm, "Scan with Tekly Go")
        if org_name:
            c.drawString(tx, y + 1.8 * mm, _fit(c, org_name, "Helvetica", 6, tw))
    c.showPage()
    c.save()
    return buf.getvalue()


# ── report ───────────────────────────────────────────────────────────────────
def day_range(start: date, end: date) -> tuple[datetime, datetime]:
    """[start 00:00, end+1 00:00) in the server's clock (UTC, like every created_at)."""
    return datetime.combine(start, datetime.min.time()), datetime.combine(end + timedelta(days=1), datetime.min.time())


def _share(go: int, total: int) -> Optional[float]:
    return round(go / total, 3) if total else None


def _blank(eid: str, name: str) -> dict:
    return {"employee_id": eid, "name": name,
            "status_changes": {"total": 0, "go": 0, "share_go": None},
            "photos": {"total": 0, "go": 0},
            "messages": {"total": 0, "go": 0},
            "scans": {"total": 0, "on_pilot_jobs": 0},
            "counter_lookups": {**{k: 0 for k in SEARCH_KINDS}, "scan": 0, "total": 0}}


async def pilot_job_ids(db, org_id: str, until: datetime) -> list[str]:
    """Jobs that got a pilot label printed (up to the end of the range)."""
    rows = (await db.execute(select(AuditLog.entity_id).where(
        AuditLog.org_id == org_id, AuditLog.action == LABELS,
        AuditLog.created_at < until).distinct())).scalars().all()
    return [r for r in rows if r]


async def go_report(db, org_id: str, start: date, end: date, *,
                    order_ids: Optional[Iterable[str]] = None,
                    employee_ids: Optional[Iterable[str]] = None) -> dict:
    """Per-employee counts of what was done on Tekly Go vs everywhere, on the pilot's jobs.

    Jobs: `order_ids` if given (only this org's count), else every job that got a pilot label.
    Lookups (scans and counter searches) aren't tied to one job, so they cover the whole range.
    The headline counts the employees in `employee_ids` (e.g. just the techs) when given.
    """
    t0, t1 = day_range(start, end)
    if order_ids is not None:
        wanted = [i for i in dict.fromkeys(order_ids) if i]
        jobs = (await db.execute(select(Order.id).where(
            Order.org_id == org_id, Order.id.in_(wanted)))).scalars().all() if wanted else []
        source = "given"
    else:
        jobs = await pilot_job_ids(db, org_id, t1)
        source = "labelled"
    jobset = set(jobs)

    staff = dict((await db.execute(select(Employee.id, Employee.name).where(
        Employee.org_id == org_id))).all())
    people: dict[str, dict] = {}

    def row(eid: Optional[str]) -> Optional[dict]:
        if not eid or eid not in staff:
            return None                     # customers (portal) and automations aren't people here
        if eid not in people:
            people[eid] = _blank(eid, staff[eid])
        return people[eid]

    if jobset:
        for eid, client in (await db.execute(
                select(OrderStatusLog.changed_by_id, OrderStatusLog.client)
                .join(Order, Order.id == OrderStatusLog.order_id)
                .where(Order.org_id == org_id, OrderStatusLog.order_id.in_(jobset),
                       OrderStatusLog.from_status.is_not(None),        # a change, not creation
                       OrderStatusLog.changed_at >= t0, OrderStatusLog.changed_at < t1))).all():
            r = row(eid)
            if r:
                r["status_changes"]["total"] += 1
                r["status_changes"]["go"] += int(is_go(client))

        for eid, client, diff in (await db.execute(
                select(AuditLog.actor_id, AuditLog.client, AuditLog.diff).where(
                    AuditLog.org_id == org_id, AuditLog.action == PHOTOS,
                    AuditLog.entity_id.in_(jobset),
                    AuditLog.created_at >= t0, AuditLog.created_at < t1))).all():
            r = row(eid)
            if r:
                n = int((diff or {}).get("count") or 0)
                r["photos"]["total"] += n
                r["photos"]["go"] += n if is_go(client) else 0

        for eid, client in (await db.execute(
                select(OrderComment.author_id, OrderComment.client)
                .join(Order, Order.id == OrderComment.order_id)
                .where(Order.org_id == org_id, OrderComment.order_id.in_(jobset),
                       OrderComment.visible_to_client == True,  # noqa: E712 — messages to the customer
                       OrderComment.created_at >= t0, OrderComment.created_at < t1))).all():
            r = row(eid)
            if r:
                r["messages"]["total"] += 1
                r["messages"]["go"] += int(is_go(client))

    for eid, client, oid, diff in (await db.execute(
            select(AuditLog.actor_id, AuditLog.client, AuditLog.entity_id, AuditLog.diff).where(
                AuditLog.org_id == org_id, AuditLog.action == LOOKUP,
                AuditLog.created_at >= t0, AuditLog.created_at < t1))).all():
        r = row(eid)
        if not r:
            continue
        d = diff or {}
        if is_go(client):
            if d.get("via") == "scan":
                r["scans"]["total"] += 1
                r["scans"]["on_pilot_jobs"] += int(bool(oid) and oid in jobset)
            continue
        kind = "scan" if d.get("via") == "scan" else (d.get("by") if d.get("by") in SEARCH_KINDS else "other")
        r["counter_lookups"][kind] += 1
        r["counter_lookups"]["total"] += 1

    for r in people.values():
        s = r["status_changes"]
        s["share_go"] = _share(s["go"], s["total"])
    rows = sorted(people.values(), key=lambda r: (-r["status_changes"]["total"], (r["name"] or "").lower()))

    only = set(employee_ids) if employee_ids else None
    counted = [r for r in rows if only is None or r["employee_id"] in only]
    total = sum(r["status_changes"]["total"] for r in counted)
    go = sum(r["status_changes"]["go"] for r in counted)
    ptotal = sum(r["photos"]["total"] for r in counted)
    pgo = sum(r["photos"]["go"] for r in counted)
    share = _share(go, total)
    if share is None:
        verdict = "not_enough_data"
        reading = "No status changes on the pilot's jobs in this range yet."
    elif share >= DECISION_THRESHOLD:
        verdict = "scan_and_timer"
        reading = (f"{round(share * 100)}% of status changes were made on a phone, at or above "
                   f"{round(DECISION_THRESHOLD * 100)}%. Techs do use their phones: keep the Expo app "
                   "centred on scan + timer.")
    else:
        verdict = "counter_and_owner"
        reading = (f"{round(share * 100)}% of status changes were made on a phone, below "
                   f"{round(DECISION_THRESHOLD * 100)}%. Re-centre the Expo app on the counter and "
                   "owner: search, check-in and push.")

    lookups = {**{k: 0 for k in SEARCH_KINDS}, "scan": 0, "total": 0}
    for r in rows:
        for k, v in r["counter_lookups"].items():
            lookups[k] += v

    return {
        "range": {"from": start.isoformat(), "to": end.isoformat(), "timezone": "UTC"},
        "jobs": {"source": source, "count": len(jobset), "ids": sorted(jobset)},
        "headline": {
            "status_changes_total": total, "status_changes_go": go,
            "share_status_changes_on_phone": share,
            "photos_total": ptotal, "photos_go": pgo, "share_photos_on_phone": _share(pgo, ptotal),
            "threshold": DECISION_THRESHOLD, "verdict": verdict, "reading": reading,
            "employees_counted": "selected" if only is not None else "all",
        },
        "counter_lookups": lookups,
        "employees": rows,
    }


CSV_COLUMNS = ["employee", "status_changes_total", "status_changes_go", "share_go",
               "photos_total", "photos_go", "messages_total", "messages_go",
               "scans_go", "scans_on_pilot_jobs",
               *[f"lookups_{k}" for k in (*SEARCH_KINDS, "scan")], "lookups_total"]


def report_csv(rep: dict) -> str:
    import csv
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(CSV_COLUMNS)

    def line(name: str, r: dict) -> list:
        s, p, m, sc, lk = r["status_changes"], r["photos"], r["messages"], r["scans"], r["counter_lookups"]
        return [name, s["total"], s["go"], "" if s["share_go"] is None else s["share_go"],
                p["total"], p["go"], m["total"], m["go"], sc["total"], sc["on_pilot_jobs"],
                *[lk[k] for k in (*SEARCH_KINDS, "scan")], lk["total"]]

    tot = _blank("", "")
    for r in rep["employees"]:
        w.writerow(line(r["name"], r))
        for sect in ("status_changes", "photos", "messages", "scans", "counter_lookups"):
            for k, v in r[sect].items():
                if k != "share_go":
                    tot[sect][k] += v
    tot["status_changes"]["share_go"] = _share(tot["status_changes"]["go"], tot["status_changes"]["total"])
    w.writerow(line("ALL STAFF", tot))
    h = rep["headline"]
    w.writerow([])
    w.writerow(["headline_share_status_changes_on_phone",
                "" if h["share_status_changes_on_phone"] is None else h["share_status_changes_on_phone"],
                "threshold", h["threshold"], "verdict", h["verdict"]])
    w.writerow(["range", rep["range"]["from"], rep["range"]["to"], "jobs", rep["jobs"]["count"], rep["jobs"]["source"]])
    return out.getvalue()
