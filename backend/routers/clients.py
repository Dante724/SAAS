from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.db.database import get_db
from backend.models import Client, Device, Employee
from backend.services.auth import get_current_employee, require_perm, record_scope
from backend.services.money import q2
from backend.core.spreadsheet import MAX_IMPORT_ROWS, safe_row

router = APIRouter(prefix="/api/clients", tags=["clients"],
                   dependencies=[Depends(record_scope)])   # role record scope on {order_id}/{client_id}

class ClientIn(BaseModel):
    name: str
    client_type: str = "person"
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    source: Optional[str] = None
    birthday: Optional[str] = None   # 144ja — ISO YYYY-MM-DD; drives birthday notifications
    loyalty_tier: str = "standard"
    consent_phone: Optional[bool] = None
    consent_email: Optional[bool] = None
    notes: Optional[str] = None
    is_supplier: bool = False
    currency_code: Optional[str] = None   # Build 122: preferred billing currency (None → base)
    manager_id: Optional[str] = None
    payer_id: Optional[str] = None
    discount_card: Optional[str] = None
    disc_services_mode: str = "none"
    disc_services_value: float = 0.0
    disc_services_price_type_id: Optional[str] = None
    disc_materials_mode: str = "none"
    disc_materials_value: float = 0.0
    disc_materials_price_type_id: Optional[str] = None
    tax_id: Optional[str] = None
    reg_no: Optional[str] = None
    billing_address: Optional[str] = None
    custom_data: Optional[dict] = None

class DeviceIn(BaseModel):
    brand: Optional[str] = None
    model: Optional[str] = None
    serial: Optional[str] = None
    imei: Optional[str] = None
    vin: Optional[str] = None
    hin: Optional[str] = None
    asset_type: Optional[str] = None
    color: Optional[str] = None
    condition: Optional[str] = None
    template_key: Optional[str] = None
    asset_group_id: Optional[str] = None
    custom_data: Optional[dict] = None

def _ser_client(c: Client, supplier=None, order_count=None) -> dict:
    return {
        # Build 144am — supplier role facet: the mirrored PO-facing Supplier row (lead
        # time, PO currency, what we owe). None when this contact isn't a supplier. Lets
        # the one contact form edit supplier fields under a Supplier tab.
        "supplier": ({"id": supplier.id, "contact_name": supplier.contact_name,
                      "lead_days": supplier.lead_days, "currency": supplier.currency,
                      "balance": float(supplier.balance or 0)} if supplier else None),
        "id": c.id, "name": c.name, "client_type": c.client_type, "phone": c.phone, "email": c.email,
        "address": c.address, "source": c.source, "birthday": c.birthday, "loyalty_tier": c.loyalty_tier,
        "balance": c.balance, "blacklisted": c.blacklisted, "notes": c.notes,
        "watch_out": getattr(c, "watch_out", None),
        "is_supplier": c.is_supplier, "manager_id": c.manager_id, "payer_id": c.payer_id,
        "currency_code": c.currency_code,
        "discount_card": c.discount_card,
        "disc_services_mode": c.disc_services_mode, "disc_services_value": c.disc_services_value,
        "disc_services_price_type_id": c.disc_services_price_type_id,
        "disc_materials_mode": c.disc_materials_mode, "disc_materials_value": c.disc_materials_value,
        "disc_materials_price_type_id": c.disc_materials_price_type_id,
        "created_at": c.created_at.isoformat(),
        "custom_data": c.custom_data or {},
        "devices": [{"id": d.id, "brand": d.brand, "model": d.model, "serial": d.serial,
                     "imei": d.imei, "vin": d.vin, "hin": d.hin, "condition": d.condition,
                     "custom_data": d.custom_data or {}} for d in c.devices],
        "order_count": len(c.orders) if order_count is None else order_count,
    }

@router.get("")
async def list_clients(
    search: Optional[str] = None,
    role: str = "all",                 # Build 114: all | client | supplier
    skip: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=1000),
    q: Optional[str] = Query(None, alias="q"),   # the pickers send ?q=; same as ?search=
    sort: str = "name",                # name | recent (pickers list the newest clients before you type)
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    from backend.services import scoping
    scope_role = await scoping.role_for(db, current)
    search = (search or q or "").strip() or None

    q = (select(Client)
         .where(Client.org_id == current.org_id)
         .options(selectinload(Client.devices))   # order counts come from one grouped query below
         .order_by(Client.created_at.desc() if sort == "recent" else Client.name))
    q = scoping.scope_clients(q, scope_role, current)
    if role == "supplier":
        q = q.where(Client.is_supplier == True)  # noqa: E712
    elif role == "client":
        q = q.where(Client.is_supplier == False)  # noqa: E712
    if search:
        q = q.where(or_(Client.name.icontains(search), Client.phone.icontains(search),
                         Client.email.icontains(search), Client.discount_card.icontains(search)))
    result = await db.execute(q.offset(skip).limit(limit))
    clients = result.scalars().all()
    count_q = select(func.count()).select_from(Client).where(Client.org_id == current.org_id)
    count_q = scoping.scope_clients(count_q, scope_role, current)   # count must respect the same scope
    if role == "supplier":
        count_q = count_q.where(Client.is_supplier == True)  # noqa: E712
    elif role == "client":
        count_q = count_q.where(Client.is_supplier == False)  # noqa: E712
    total = (await db.execute(count_q)).scalar()
    from backend.models import Order
    ids = [c.id for c in clients]
    counts = dict((await db.execute(select(Order.client_id, func.count()).where(
        Order.client_id.in_(ids)).group_by(Order.client_id))).all()) if ids else {}
    return {"total": total, "items": [_ser_client(c, order_count=counts.get(c.id, 0)) for c in clients]}

@router.post("", status_code=201)
async def create_client(
    data: ClientIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    from backend.models import Organisation
    org = (await db.execute(select(Organisation).where(
        Organisation.id == current.org_id))).scalar_one()
    # Duplicate guards (Orderry: prohibit creating a contact with an existing phone/email)
    if org.prohibit_dup_phone and data.phone:
        dup = (await db.execute(select(Client).where(
            Client.org_id == current.org_id, Client.phone == data.phone))).scalar_one_or_none()
        if dup:
            raise HTTPException(409, "A contact with this phone number already exists")
    if org.prohibit_dup_email and data.email:
        dup = (await db.execute(select(Client).where(
            Client.org_id == current.org_id, Client.email == data.email))).scalar_one_or_none()
        if dup:
            raise HTTPException(409, "A contact with this email already exists")
    payload = data.model_dump()
    # drop None consent so org-policy setdefault applies
    for k in ("consent_phone", "consent_email"):
        if payload.get(k) is None:
            payload.pop(k, None)
    # 144gp — enforce custom-field rules (required / lock / conditional visibility).
    from backend.routers.forms import resolve_custom_data, check_entity_required
    await check_entity_required(db, current.org_id, "client", data)
    payload["custom_data"] = await resolve_custom_data(
        db, current.org_id, "client", data.custom_data, creating=True)
    # Consent defaults from org policy unless explicitly provided
    payload.setdefault("consent_phone", org.contact_consent_phone_default)
    payload.setdefault("consent_email", org.contact_consent_email_default)
    client = Client(org_id=current.org_id, **payload)
    db.add(client)
    await db.flush()
    await _sync_supplier(db, current.org_id, client)   # Build 114: mirror to PO-facing Supplier
    await db.commit()
    await db.refresh(client)
    from backend.services.audit import record as _audit
    await _audit(db, org_id=current.org_id, actor_id=current.id, action="client.create",
                 entity_type="client", entity_id=client.id, diff={"name": client.name})
    await db.commit()
    return {"id": client.id, "name": client.name}


@router.get("/duplicates")
async def duplicate_phones(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """C: surface clients sharing a phone so staff can merge them. Phone is the
    universal match key; duplicates silently break notifications and call history.
    Defined before /{client_id} so the literal path wins over the wildcard."""
    rows = (await db.execute(
        select(Client.phone, func.count(Client.id)).where(
            Client.org_id == current.org_id, Client.phone.isnot(None), Client.phone != "")
        .group_by(Client.phone).having(func.count(Client.id) > 1))).all()
    clusters = []
    for phone, n in rows:
        members = (await db.execute(select(Client).where(
            Client.org_id == current.org_id, Client.phone == phone)
            .order_by(Client.created_at))).scalars().all()
        clusters.append({
            "phone": phone, "count": n,
            "keep_hint_id": members[-1].id if members else None,  # latest-created wins
            "clients": [{"id": c.id, "name": c.name,
                         "created_at": c.created_at.isoformat() if c.created_at else None}
                        for c in members],
        })
    return {"items": clusters, "total": len(clusters)}


@router.get("/{client_id}")
async def get_client(
    client_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    result = await db.execute(
        select(Client).where(Client.id == client_id, Client.org_id == current.org_id)
        .options(selectinload(Client.devices), selectinload(Client.orders))
    )
    client = result.scalar_one_or_none()
    if not client:
        raise HTTPException(404, "Client not found")
    from backend.services import scoping
    if not await scoping.can_view_client(db, current, client):
        raise HTTPException(403, "You can only open contacts you manage")
    from backend.models import Supplier
    mirror = (await db.execute(select(Supplier).where(
        Supplier.org_id == current.org_id, Supplier.client_id == client.id))).scalars().first()
    return _ser_client(client, supplier=mirror)

@router.post("/{client_id}/portal-link")
async def issue_portal_link(
    client_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Issue (or rotate) a customer-portal magic link for this client.

    Returns a durable token; the relative URL `/portal/{token}` lets the customer
    see all their work orders/estimates/invoices and approve estimates without a login.
    """
    import secrets
    client = (await db.execute(
        select(Client).where(Client.id == client_id, Client.org_id == current.org_id)
    )).scalar_one_or_none()
    if not client:
        raise HTTPException(404, "Client not found")
    client.portal_token = secrets.token_urlsafe(24)
    await db.commit()
    return {"portal_token": client.portal_token, "portal_url": f"/portal/{client.portal_token}"}


@router.put("/{client_id}")
async def update_client(
    client_id: str, data: ClientIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    result = await db.execute(select(Client).where(Client.id == client_id, Client.org_id == current.org_id))
    client = result.scalar_one_or_none()
    if not client:
        raise HTTPException(404, "Client not found")
    for k, v in data.model_dump().items():
        if v is None and k in ("custom_data", "consent_phone", "consent_email"):
            continue  # don't wipe values / null NOT-NULL consent on a partial update
        if k == "custom_data":
            continue  # handled below through the rule resolver
        setattr(client, k, v)
    if data.custom_data is not None:
        from backend.routers.forms import resolve_custom_data
        client.custom_data = await resolve_custom_data(
            db, current.org_id, "client", data.custom_data, existing=client.custom_data or {})
    await db.flush()
    await _sync_supplier(db, current.org_id, client)   # Build 114: keep Supplier mirror in sync
    await db.commit()
    return {"ok": True}


class ClientPatch(BaseModel):
    # partial update — only fields explicitly sent are applied (exclude_unset)
    name: Optional[str] = None
    client_type: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    source: Optional[str] = None
    birthday: Optional[str] = None
    loyalty_tier: Optional[str] = None
    notes: Optional[str] = None
    watch_out: Optional[str] = None
    is_supplier: Optional[bool] = None
    blacklisted: Optional[bool] = None
    currency_code: Optional[str] = None
    manager_id: Optional[str] = None
    payer_id: Optional[str] = None
    discount_card: Optional[str] = None
    disc_services_mode: Optional[str] = None
    disc_services_value: Optional[float] = None
    disc_services_price_type_id: Optional[str] = None
    disc_materials_mode: Optional[str] = None
    disc_materials_value: Optional[float] = None
    disc_materials_price_type_id: Optional[str] = None
    tax_id: Optional[str] = None
    reg_no: Optional[str] = None
    billing_address: Optional[str] = None
    custom_data: Optional[dict] = None


@router.patch("/{client_id}")
async def patch_client(
    client_id: str, data: ClientPatch,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Partial update — unlike PUT, only the fields sent are touched. Powers the
    client drawer's inline edits (custom data + the Orderry-style discount matrix)."""
    client = (await db.execute(select(Client).where(
        Client.id == client_id, Client.org_id == current.org_id))).scalar_one_or_none()
    if not client:
        raise HTTPException(404, "Client not found")
    fields = data.model_dump(exclude_unset=True)
    cd = fields.pop("custom_data", None)
    for k, v in fields.items():
        setattr(client, k, v)
    if cd is not None:
        from backend.routers.forms import resolve_custom_data
        client.custom_data = await resolve_custom_data(
            db, current.org_id, "client", cd, existing=client.custom_data or {})
    await db.flush()
    await _sync_supplier(db, current.org_id, client)
    await db.commit()
    return {"ok": True}


async def _sync_supplier(db: AsyncSession, org_id: str, client: Client) -> None:
    """Keep a PO-facing Supplier row in sync with a Contact marked as a supplier.

    A contact can be both a client and a supplier. Purchase orders reference a Supplier
    record, so when a contact is flagged is_supplier we ensure a linked Supplier exists
    (and mirror its name/phone/email); when the flag is cleared we leave any existing
    Supplier in place if it has purchase-order history, otherwise remove the mirror.
    """
    from backend.models import PurchaseOrder, Supplier

    existing = (await db.execute(select(Supplier).where(
        Supplier.org_id == org_id, Supplier.client_id == client.id))).scalars().first()
    if client.is_supplier:
        if existing:
            existing.name = client.name
            existing.phone = client.phone
            existing.email = client.email
        else:
            db.add(Supplier(org_id=org_id, client_id=client.id, name=client.name,
                            phone=client.phone, email=client.email, balance=0))
    elif existing:
        has_pos = (await db.execute(select(func.count()).select_from(PurchaseOrder).where(
            PurchaseOrder.supplier_id == existing.id))).scalar() or 0
        if not has_pos:
            await db.delete(existing)

@router.delete("/{client_id}")
async def delete_client(
    client_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_delete_clients")),
):
    result = await db.execute(select(Client).where(Client.id == client_id, Client.org_id == current.org_id))
    client = result.scalar_one_or_none()
    if not client:
        raise HTTPException(404, "Client not found")
    # 144aw: a contact with any history (orders, invoices, payments, devices, ledger, …) must not
    # be hard-deleted — doing so would orphan or cascade financial history and, under enforced FKs,
    # crash with an IntegrityError. Discover every table that references clients.id from the mapper
    # so no new table is ever missed, and refuse if anything points here. Merge is the safe path.
    for cls, col in _client_fk_targets():
        if cls is Client and col == "id":
            continue
        n = (await db.execute(select(func.count()).select_from(cls)
             .where(getattr(cls, col) == client_id))).scalar() or 0
        if n:
            raise HTTPException(409, "This contact has history (orders, invoices, payments or "
                                     "devices) and can't be deleted. Merge it into another contact "
                                     "instead to preserve the records.")
    await db.delete(client)
    await db.commit()
    return {"ok": True}

@router.post("/{client_id}/devices", status_code=201)
async def add_device(
    client_id: str, data: DeviceIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    result = await db.execute(select(Client).where(Client.id == client_id, Client.org_id == current.org_id))
    if not result.scalar_one_or_none():
        raise HTTPException(404, "Client not found")
    from backend.models import Organisation
    from backend.services import industry as _ind
    org = (await db.execute(select(Organisation).where(Organisation.id == current.org_id))).scalar_one_or_none()
    payload = data.model_dump()
    payload["template_key"] = _ind.normalize_template_key(
        payload.get("template_key") or (org.industry_template if org else None))
    if payload.get("custom_data") is None:
        payload.pop("custom_data", None)
    device = Device(org_id=current.org_id, client_id=client_id, **payload)
    db.add(device)
    await db.commit()
    await db.refresh(device)
    return {"id": device.id}


@router.get("/{client_id}/documents")
async def client_documents(
    client_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """All work orders for this client (Orderry's Documents tab in the contact profile)."""
    from backend.models import Order
    orders = (await db.execute(
        select(Order).where(Order.client_id == client_id, Order.org_id == current.org_id)
        .order_by(Order.created_at.desc())
    )).scalars().all()
    return {"items": [{"id": o.id, "order_number": o.order_number, "status": o.status,
                       "total": o.total, "created_at": o.created_at.isoformat(),
                       "closed_at": o.closed_at.isoformat() if o.closed_at else None} for o in orders]}


@router.get("/{client_id}/purchases")
async def client_purchases(
    client_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Build 144aj — everything this client has bought from us: line items from both
    point-of-sale Sales and work-order billing, unified and newest-first, with a
    spend summary. Powers the contact profile's Purchases tab."""
    # tenancy: the client must belong to this org
    client = (await db.execute(select(Client).where(
        Client.id == client_id, Client.org_id == current.org_id))).scalar_one_or_none()
    if not client:
        raise HTTPException(404, "Client not found")

    from backend.models import Order, Sale, SaleLine

    items: list[dict] = []

    # ── POS sales ──
    sales = (await db.execute(
        select(Sale).where(Sale.client_id == client_id, Sale.org_id == current.org_id)
        .order_by(Sale.created_at.desc()))).scalars().all()
    # Fetch every sale's lines in ONE query, then group — avoids an N+1 (a query per sale).
    sale_lines: dict[str, list] = {}
    if sales:
        for ln in (await db.execute(select(SaleLine).where(
                SaleLine.sale_id.in_([s.id for s in sales])))).scalars().all():
            sale_lines.setdefault(ln.sale_id, []).append(ln)
    for s in sales:
        for ln in sale_lines.get(s.id, []):
            items.append({
                "date": s.created_at.isoformat(),
                "source_type": "sale", "source_id": s.id, "source_ref": s.sale_number,
                "item": ln.description or "(item)", "kind": ln.kind or "product",
                "qty": ln.qty, "unit_price": ln.unit_price, "total": ln.total,
            })

    # ── work-order billing (parts, labour, fees the customer was charged for) ──
    orders = (await db.execute(
        select(Order).where(Order.client_id == client_id, Order.org_id == current.org_id)
        .options(selectinload(Order.lines)).order_by(Order.created_at.desc())
    )).scalars().all()
    kind_of = {"part": "product", "labour": "service", "fee": "fee"}
    for o in orders:
        for ln in (o.lines or []):
            if ln.line_type not in ("part", "labour", "fee"):
                continue
            items.append({
                "date": o.created_at.isoformat(),
                "source_type": "order", "source_id": o.id, "source_ref": o.order_number,
                "item": ln.description or "(item)",
                "kind": ln.kind or kind_of.get(ln.line_type, "product"),
                "qty": ln.qty, "unit_price": ln.unit_price, "total": ln.total,
            })

    items.sort(key=lambda x: x["date"], reverse=True)
    total_spent = q2(sum(i["total"] or 0 for i in items))
    dates = [i["date"] for i in items]
    return {
        "items": items,
        "summary": {
            "total_spent": total_spent,
            "item_count": len(items),
            "transaction_count": len({(i["source_type"], i["source_id"]) for i in items}),
            "first_at": min(dates) if dates else None,
            "last_at": max(dates) if dates else None,
        },
    }


# ── Build 82: CRM data ops — import, merge, export ───────────────────────────
class ImportIn(BaseModel):
    rows: list[dict] = Field(max_length=MAX_IMPORT_ROWS)  # raw rows as parsed from the CSV/sheet
    mapping: dict[str, str] = {}     # {source_column: client_field}
    update_existing: bool = True     # match on phone/email and update rather than duplicate
    dry_run: bool = False            # preview the outcome without writing


IMPORTABLE = {"name", "phone", "email", "address", "notes", "source", "discount_card"}


@router.post("/import")
async def import_clients(
    data: ImportIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Import contacts from a CSV/sheet with an explicit column mapping.

    Existing contacts are matched on **phone, then email** and (by default) updated
    in place rather than duplicated — this is Orderry's "update by import" behaviour.
    `dry_run` returns exactly what *would* happen without touching anything, so the
    UI can show a preview before committing.
    """
    unknown = set(data.mapping.values()) - IMPORTABLE
    if unknown:
        raise HTTPException(422, f"Can't map to unknown fields: {sorted(unknown)}")

    created = updated = skipped = 0
    errors: list[dict] = []
    preview: list[dict] = []

    for i, raw in enumerate(data.rows):
        # apply the mapping (source column → client field)
        rec = {}
        for src, field in data.mapping.items():
            val = raw.get(src)
            if val not in (None, ""):
                rec[field] = str(val).strip()
        if not rec.get("name"):
            errors.append({"row": i + 1, "error": "Missing name"})
            skipped += 1
            continue
        if not rec.get("phone") and not rec.get("email"):
            errors.append({"row": i + 1, "error": "Needs a phone or an email"})
            skipped += 1
            continue

        existing = None
        if rec.get("phone"):
            existing = (await db.execute(select(Client).where(
                Client.org_id == current.org_id, Client.phone == rec["phone"]))).scalars().first()
        if not existing and rec.get("email"):
            existing = (await db.execute(select(Client).where(
                Client.org_id == current.org_id, Client.email == rec["email"]))).scalars().first()

        if existing and not data.update_existing:
            skipped += 1
            preview.append({"row": i + 1, "action": "skip", "name": rec["name"]})
            continue
        if existing:
            updated += 1
            preview.append({"row": i + 1, "action": "update", "name": rec["name"],
                            "client_id": existing.id})
            if not data.dry_run:
                for k, v in rec.items():
                    # never blank out an existing value with an empty import cell
                    if v:
                        setattr(existing, k, v)
        else:
            created += 1
            preview.append({"row": i + 1, "action": "create", "name": rec["name"]})
            if not data.dry_run:
                db.add(Client(org_id=current.org_id, **rec))

    if not data.dry_run:
        await db.commit()
    return {"ok": True, "dry_run": data.dry_run, "created": created, "updated": updated,
            "skipped": skipped, "errors": errors, "preview": preview[:100]}


class MergeClientsIn(BaseModel):
    target_id: str
    source_ids: list[str]


_MERGE_FILL_FIELDS = ("phone", "email", "address", "notes", "discount_card", "source", "manager_id")


async def _resolve_merge(db, org_id: str, data: MergeClientsIn):
    """Shared validation for merge + its preview. Returns (target, sources)."""
    if not data.source_ids:
        raise HTTPException(400, "Pick at least one contact to merge in")
    if data.target_id in data.source_ids:
        raise HTTPException(400, "The target can't also be a source")
    target = (await db.execute(select(Client).where(
        Client.id == data.target_id, Client.org_id == org_id))).scalar_one_or_none()
    if not target:
        raise HTTPException(404, "Target contact not found")
    sources = (await db.execute(select(Client).where(
        Client.id.in_(data.source_ids), Client.org_id == org_id))).scalars().all()
    if len(sources) != len(data.source_ids):
        raise HTTPException(404, "One or more source contacts not found")
    return target, sources


def _snapshot_client(c: Client) -> dict:
    """Full column dict of a contact (datetimes as ISO strings) so it can be recreated verbatim."""
    from datetime import date as _date
    out = {}
    for col in Client.__table__.columns:
        v = getattr(c, col.name)
        out[col.name] = v.isoformat() if isinstance(v, (datetime, _date)) else v
    return out


def _restore_client(snap: dict) -> Client:
    """Rebuild a Client from a snapshot, converting ISO strings back for DateTime columns."""
    from sqlalchemy import DateTime as _DT
    cols = {c.name: c for c in Client.__table__.columns}
    kwargs = {}
    for k, v in snap.items():
        col = cols.get(k)
        if col is None:
            continue
        if v is not None and isinstance(col.type, _DT) and isinstance(v, str):
            v = datetime.fromisoformat(v)
        kwargs[k] = v
    return Client(**kwargs)


def _client_fk_targets():
    """Every (model, column) in the schema that foreign-keys to clients.id — discovered from
    the mapper so the merge can never silently miss a table as new ones are added. This is the
    COMPLETE set a merge must re-point before deleting a source (orphaning any of these would
    lose history on SQLite and raise an IntegrityError on Postgres). Includes the second FK on
    Invoice (payer_client_id) and the Client self-reference (payer_id)."""
    from sqlalchemy import inspect as _inspect

    import backend.models as _m
    out = []
    for name in dir(_m):
        cls = getattr(_m, name)
        if not isinstance(cls, type) or not hasattr(cls, "__tablename__"):
            continue
        try:
            mapper = _inspect(cls)
        except Exception:
            continue
        for col in mapper.columns:
            if any(fk.column.table.name == "clients" for fk in col.foreign_keys):
                out.append((cls, col.key))
    return out


# Friendly UI labels for the main categories the merge-preview dialog shows by name.
_MOVE_LABELS = {"Device.client_id": "devices", "Order.client_id": "orders",
                "Invoice.client_id": "invoices", "Payment.client_id": "payments",
                "Feedback.client_id": "feedback", "ClientLedgerEntry.client_id": "ledger"}


@router.post("/merge/preview")
async def merge_clients_preview(
    data: MergeClientsIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Dry-run a merge: report exactly what *would* move and which blank target fields would be
    filled, without changing anything. The UI shows this before the irreversible merge runs."""
    from sqlalchemy import func as _func
    target, sources = await _resolve_merge(db, current.org_id, data)
    sids = [s.id for s in sources]

    # Count every client-linked table that will move. The six main categories keep their
    # friendly label for the dialog; everything else rolls up into "other records" so the
    # preview never understates what a merge actually touches.
    moved = {"devices": 0, "orders": 0, "invoices": 0, "payments": 0, "feedback": 0, "ledger": 0}
    other = 0
    for model, attr in _client_fk_targets():
        if model is Client:
            continue   # the self-ref payer_id is re-pointed but not "history" to surface
        n = int((await db.execute(select(_func.count()).select_from(model).where(
            getattr(model, attr).in_(sids)))).scalar() or 0)
        label = _MOVE_LABELS.get(f"{model.__name__}.{attr}")
        if label:
            moved[label] += n
        else:
            other += n
    if other:
        moved["other records"] = other

    # Simulate the fill loop (first source with a value wins, target's own value is never overwritten).
    fills, working = [], {f: getattr(target, f, None) for f in _MERGE_FILL_FIELDS}
    for s in sources:
        for f in _MERGE_FILL_FIELDS:
            if not working[f] and getattr(s, f, None):
                working[f] = getattr(s, f)
                fills.append({"field": f, "value": getattr(s, f), "from": s.name})

    src_balance = sum(float(s.balance or 0) for s in sources)
    return {
        "target": {"id": target.id, "name": target.name, "balance": float(target.balance or 0)},
        "sources": [{"id": s.id, "name": s.name, "balance": float(s.balance or 0)} for s in sources],
        "moved": moved,
        "field_fills": fills,
        "result_balance": float(target.balance or 0) + src_balance,
    }


@router.post("/merge")
async def merge_clients(
    data: MergeClientsIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_delete_clients")),
):
    """Merge duplicate contacts into one — now reversible.

    Devices, orders, invoices, payments, feedback and balance entries are re-pointed at the
    target; blank target fields fill from the sources (real values are never overwritten);
    balances are summed. Before the sources are deleted we snapshot them and record exactly
    what moved into a MergeLog, so the merge can be undone (POST /clients/merge/{id}/undo).
    """
    from backend.models import MergeLog
    target, sources = await _resolve_merge(db, current.org_id, data)
    source_ids = {s.id for s in sources}

    # What the target looked like before, so undo can put it back.
    target_before = {f: getattr(target, f, None) for f in _MERGE_FILL_FIELDS}
    target_before["balance"] = float(target.balance or 0)

    fk_targets = _client_fk_targets()
    moved_counts: dict = {}
    # moved_map: { "Model.column": { record_id: original_source_id } } — enough for undo to
    # put every re-pointed row back on the exact source it came from.
    moved_map: dict = {}
    snapshots = []
    for s in sources:
        snapshots.append(_snapshot_client(s))
        for field in _MERGE_FILL_FIELDS:
            if not getattr(target, field, None) and getattr(s, field, None):
                setattr(target, field, getattr(s, field))
        target.balance = (target.balance or 0) + (s.balance or 0)

        for model, attr in fk_targets:
            key = f"{model.__name__}.{attr}"
            rows = (await db.execute(select(model).where(getattr(model, attr) == s.id))).scalars().all()
            for r in rows:
                # Never touch a source Client row itself — it's about to be deleted.
                if model is Client and r.id in source_ids:
                    continue
                setattr(r, attr, target.id)
                moved_map.setdefault(key, {})[r.id] = s.id
                moved_counts[key] = moved_counts.get(key, 0) + 1
        await db.delete(s)

    # Guard the self-reference edge: if the target's own payer was one of the merged sources,
    # it now points at itself — clear it rather than leave a self-payer.
    if getattr(target, "payer_id", None) == target.id:
        target.payer_id = None

    log = MergeLog(org_id=current.org_id, target_id=target.id, source_snapshots=snapshots,
                   moved=moved_map, target_before=target_before, created_by_id=current.id)
    db.add(log)
    await db.commit()
    friendly = {lbl: moved_counts.get(k, 0) for k, lbl in _MOVE_LABELS.items()}
    friendly["other"] = sum(n for k, n in moved_counts.items() if k not in _MOVE_LABELS)
    return {"ok": True, "target_id": target.id, "merged": len(sources),
            "moved": friendly, "merge_log_id": log.id}


@router.post("/merge/{log_id}/undo")
async def undo_merge(
    log_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_delete_clients")),
):
    """Reverse a merge: recreate the deleted source contacts and move their records back."""
    from backend.models import MergeLog
    log = (await db.execute(select(MergeLog).where(
        MergeLog.id == log_id, MergeLog.org_id == current.org_id))).scalar_one_or_none()
    if not log:
        raise HTTPException(404, "Merge not found")
    if log.undone_at:
        raise HTTPException(400, "This merge was already undone")

    # 1) Recreate each source with its original id (skip any that somehow still exist).
    for snap in (log.source_snapshots or []):
        exists = (await db.execute(select(Client).where(Client.id == snap["id"]))).scalar_one_or_none()
        if not exists:
            db.add(_restore_client(snap))
    await db.flush()

    # 2) Move records back to their original source — only if still sitting on the target
    #    (guards against a row that was reassigned after the merge). Keyed by "Model.column"
    #    so the exact FK (incl. Invoice.payer_client_id and Client.payer_id) is reversed.
    by_key = {f"{model.__name__}.{attr}": (model, attr) for model, attr in _client_fk_targets()}
    for key, recs in (log.moved or {}).items():
        pair = by_key.get(key)
        if not pair:
            continue
        model, attr = pair
        for rid, sid in recs.items():
            row = (await db.execute(select(model).where(model.id == rid))).scalar_one_or_none()
            if row is not None and getattr(row, attr, None) == log.target_id:
                setattr(row, attr, sid)

    # 3) Restore the target — delta-reverse the balance (never clobber post-merge activity) and
    #    restore only the fields this merge actually filled AND that haven't been edited since.
    target = (await db.execute(select(Client).where(
        Client.id == log.target_id, Client.org_id == current.org_id))).scalar_one_or_none()
    if target:
        before = log.target_before or {}
        restored_balance = sum(float(s.get("balance") or 0) for s in (log.source_snapshots or []))
        target.balance = (target.balance or 0) - restored_balance
        # Recompute what the merge filled (same first-source-wins logic) to know the merged value.
        working = {f: before.get(f) for f in _MERGE_FILL_FIELDS}
        for snap in (log.source_snapshots or []):
            for f in _MERGE_FILL_FIELDS:
                if not working.get(f) and snap.get(f):
                    working[f] = snap.get(f)
        for f in _MERGE_FILL_FIELDS:
            orig, merged_val = before.get(f), working.get(f)
            if merged_val != orig and getattr(target, f, None) == merged_val:
                setattr(target, f, orig)

    log.undone_at = datetime.utcnow()
    await db.commit()
    return {"ok": True, "restored": len(log.source_snapshots or [])}


@router.get("/export/csv")
async def export_clients(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_export_clients")),
):
    """Export the contact book as CSV (respects the caller's contact scope)."""
    import csv
    import io

    from fastapi.responses import StreamingResponse

    from backend.services import scoping
    role = await scoping.role_for(db, current)
    q = select(Client).where(Client.org_id == current.org_id).order_by(Client.name)
    q = scoping.scope_clients(q, role, current)
    rows = (await db.execute(q)).scalars().all()

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["name", "phone", "email", "address", "source", "balance", "notes"])
    for c in rows:
        w.writerow(safe_row([c.name, c.phone or "", c.email or "", c.address or "",
                             c.source or "", c.balance or 0, c.notes or ""]))
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=clients.csv"})


# ── Build 81: calls + feedback history on the client profile ─────────────────
@router.get("/{client_id}/calls")
async def client_calls(
    client_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    from backend.models import Call
    rows = (await db.execute(select(Call).where(
        Call.org_id == current.org_id, Call.client_id == client_id)
        .order_by(Call.id.desc()).limit(100))).scalars().all()
    return {"items": [{
        "id": c.id, "direction": c.direction, "provider": c.provider,
        "from_number": c.from_number, "to_number": c.to_number,
        "duration_s": getattr(c, "duration_s", None),
        "recording_url": getattr(c, "recording_url", None),
        "created_at": c.created_at.isoformat() if getattr(c, "created_at", None) else None,
    } for c in rows]}


@router.get("/{client_id}/feedback")
async def client_feedback(
    client_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    from backend.models import Feedback
    rows = (await db.execute(select(Feedback).where(
        Feedback.org_id == current.org_id, Feedback.client_id == client_id)
        .order_by(Feedback.created_at.desc()).limit(50))).scalars().all()
    return {"items": [{
        "id": f.id, "order_id": f.order_id, "system": f.system, "rating": f.rating,
        "comment": f.comment,
        "submitted_at": f.submitted_at.isoformat() if f.submitted_at else None,
    } for f in rows]}


# ── Build 116: contact balance ledger — advance / payout / adjustment ─────────
@router.get("/{client_id}/balance")
async def client_balance(
    client_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    from backend.models import ClientLedgerEntry
    c = (await db.execute(select(Client).where(
        Client.id == client_id, Client.org_id == current.org_id))).scalar_one_or_none()
    if not c:
        raise HTTPException(404, "Contact not found")
    rows = (await db.execute(select(ClientLedgerEntry).where(
        ClientLedgerEntry.client_id == client_id)
        .order_by(ClientLedgerEntry.created_at.desc()).limit(200))).scalars().all()
    # Build 122: include the client's display currency so balances can show converted.
    cur = {"code": None, "symbol": "₹", "rate": 1.0}
    if c.currency_code:
        from backend.services import currency as _cur
        cur = await _cur.rate_for_code(db, current.org_id, c.currency_code)
    return {"balance": float(c.balance or 0),
            "currency": cur,
            "entries": [{"id": e.id, "entry_type": e.entry_type, "amount": float(e.amount),
                         "note": e.note, "ref_type": e.ref_type, "ref_id": e.ref_id,
                         "created_at": e.created_at.isoformat()} for e in rows]}


class BalanceMoveIn(BaseModel):
    kind: str                          # advance | payout | adjustment
    amount: float
    note: Optional[str] = None
    account_id: Optional[str] = None   # 144av: cashbox for advance (cash in) / payout (cash out)


@router.post("/{client_id}/balance", status_code=201)
async def move_balance(
    client_id: str, data: BalanceMoveIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_make_client_payments")),
):
    """Post an advance, payout or manual adjustment against a contact's balance.

    Convention: a positive balance means the client owes us. An *advance* (client pays in)
    reduces what they owe (negative movement); a *payout* (we return funds) increases it
    back (positive). An *adjustment* is signed by the caller. The running balance is
    updated atomically alongside the ledger row so the two never disagree.
    """
    from backend.models import ClientLedgerEntry
    c = (await db.execute(select(Client).where(
        Client.id == client_id, Client.org_id == current.org_id))).scalar_one_or_none()
    if not c:
        raise HTTPException(404, "Contact not found")
    if data.kind not in ("advance", "payout", "adjustment"):
        raise HTTPException(422, "kind must be advance|payout|adjustment")
    amt = abs(data.amount)
    if data.kind == "advance":
        signed = -amt
    elif data.kind == "payout":
        signed = amt
    else:
        signed = data.amount               # adjustment keeps the caller's sign
    entry = ClientLedgerEntry(org_id=current.org_id, client_id=client_id,
                              entry_type=data.kind, amount=signed, note=data.note,
                              created_by_id=current.id)
    db.add(entry)
    c.balance = float(c.balance or 0) + signed
    # 144av: an advance (client pays cash in) and a payout (we return cash) physically move the
    # drawer, so mirror them into the cashbox. An adjustment is a book correction → no cash moves.
    if data.account_id and data.kind in ("advance", "payout"):
        from backend.routers.cash import post_transaction
        delta = amt if data.kind == "advance" else -amt   # advance → + into account; payout → −
        await post_transaction(db, current.org_id, data.account_id,
                               "income" if data.kind == "advance" else "expense", delta,
                               comment=f"Client {data.kind}", ref_type="client", ref_id=client_id,
                               employee_id=current.id)
    await db.commit()
    return {"id": entry.id, "balance": float(c.balance)}


@router.get("/{client_id}/payments")
async def client_payments(
    client_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Build 124 — the contact's Payments tab: every payment, prepayment and refund
    recorded against them, newest first, each linked back to its source order. Reads the
    Payment table directly, so the history is complete (no dependence on the ledger)."""
    from backend.models import Order, Payment
    c = (await db.execute(select(Client).where(
        Client.id == client_id, Client.org_id == current.org_id))).scalar_one_or_none()
    if not c:
        raise HTTPException(404, "Contact not found")
    rows = (await db.execute(select(Payment).where(Payment.client_id == client_id)
            .order_by(Payment.paid_at.desc()).limit(200))).scalars().all()
    oids = {p.order_id for p in rows if p.order_id}
    onum = {}
    if oids:
        for o in (await db.execute(select(Order).where(Order.id.in_(oids)))).scalars().all():
            onum[o.id] = o.order_number
    return {"items": [{
        "id": p.id, "kind": p.kind, "method": p.method, "amount": float(p.amount or 0),
        "order_id": p.order_id, "order_number": onum.get(p.order_id),
        "reference": p.reference, "paid_at": p.paid_at.isoformat() if p.paid_at else None,
    } for p in rows]}


@router.get("/{client_id}/communication")
async def client_communication(
    client_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Build 126 — the contact's Communication tab: every message exchanged with them
    across all channels (whatsapp/sms/email/social/call), newest first, plus who sent
    each outbound message."""
    from backend.models import Employee as Emp
    from backend.models import InboxMessage, InboxThread
    c = (await db.execute(select(Client).where(
        Client.id == client_id, Client.org_id == current.org_id))).scalar_one_or_none()
    if not c:
        raise HTTPException(404, "Contact not found")
    threads = (await db.execute(select(InboxThread).where(
        InboxThread.org_id == current.org_id, InboxThread.client_id == client_id))).scalars().all()
    tmap = {t.id: t for t in threads}
    msgs = []
    if tmap:
        msgs = (await db.execute(select(InboxMessage).where(
            InboxMessage.thread_id.in_(list(tmap.keys())))
            .order_by(InboxMessage.created_at.desc()).limit(200))).scalars().all()
    author_ids = {m.author_id for m in msgs if m.author_id}
    names = {}
    if author_ids:
        for e in (await db.execute(select(Emp).where(Emp.id.in_(author_ids)))).scalars().all():
            names[e.id] = e.name
    return {"items": [{
        "id": m.id, "channel": tmap[m.thread_id].channel if m.thread_id in tmap else None,
        "direction": m.direction, "body": m.body,
        "author": names.get(m.author_id), "delivered": m.delivered,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    } for m in msgs]}


class MessageIn(BaseModel):
    channel: str = "email"          # email|sms|whatsapp|facebook|instagram|call
    body: str


@router.post("/{client_id}/communication", status_code=201)
async def send_client_message(
    client_id: str, data: MessageIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Log an outbound message to the contact, reusing an open thread on that channel or
    opening one. Delivery via the external provider is stubbed at the transport boundary
    (like the other channels); the message is recorded so the history is complete."""
    from backend.models import InboxMessage, InboxThread
    c = (await db.execute(select(Client).where(
        Client.id == client_id, Client.org_id == current.org_id))).scalar_one_or_none()
    if not c:
        raise HTTPException(404, "Contact not found")
    if not (data.body or "").strip():
        raise HTTPException(422, "Message body is required")
    valid = ("email", "sms", "whatsapp", "facebook", "instagram", "call")
    if data.channel not in valid:
        raise HTTPException(422, f"channel must be one of {list(valid)}")
    thread = (await db.execute(select(InboxThread).where(
        InboxThread.org_id == current.org_id, InboxThread.client_id == client_id,
        InboxThread.channel == data.channel).order_by(InboxThread.last_message_at.desc()))).scalars().first()
    if not thread:
        thread = InboxThread(org_id=current.org_id, channel=data.channel, client_id=client_id,
                             handle=(c.email if data.channel == "email" else c.phone),
                             status="open", unread=False, last_message_at=datetime.utcnow())
        db.add(thread)
        await db.flush()
    msg = InboxMessage(org_id=current.org_id, thread_id=thread.id, direction="outbound",
                       body=data.body.strip(), author_id=current.id, delivered=True)
    db.add(msg)
    thread.last_message_at = datetime.utcnow()
    await db.commit()
    return {"id": msg.id, "thread_id": thread.id, "channel": data.channel}


@router.post("/{client_id}/communication/suggest")
async def suggest_client_reply(
    client_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Build 143 (Phase 8) — AI-drafted reply for the contact. Uses the last inbound
    message as context; degrades to a deterministic stub when no model is configured. The
    draft is returned for review — it is never auto-sent (staff must press Send)."""
    from backend.models import InboxMessage, InboxThread
    from backend.services import ai
    c = (await db.execute(select(Client).where(
        Client.id == client_id, Client.org_id == current.org_id))).scalar_one_or_none()
    if not c:
        raise HTTPException(404, "Contact not found")
    tids = [t for (t,) in (await db.execute(select(InboxThread.id).where(
        InboxThread.org_id == current.org_id, InboxThread.client_id == client_id))).all()]
    last_in = None
    if tids:
        last_in = (await db.execute(select(InboxMessage).where(
            InboxMessage.thread_id.in_(tids), InboxMessage.direction == "inbound")
            .order_by(InboxMessage.created_at.desc()))).scalars().first()
    customer_message = (last_in.body if last_in else "") or "Hi, checking in on my repair."
    out = await ai.suggest_reply(customer_message=customer_message,
                                 context=f"Customer {c.name}")
    return {"provider": out.get("provider", "stub"), "reply": out.get("reply", ""),
            "ai_drafted": True}


# ── Build 143: reusable message templates (Phase 4) ──
class MessageTemplateIn(BaseModel):
    name: str
    channel: str = "any"
    body: str


@router.get("/message-templates/all")
async def list_message_templates(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    from backend.models import MessageTemplate
    rows = (await db.execute(select(MessageTemplate).where(
        MessageTemplate.org_id == current.org_id).order_by(MessageTemplate.name))).scalars().all()
    return {"items": [{"id": t.id, "name": t.name, "channel": t.channel, "body": t.body} for t in rows]}


@router.post("/message-templates/all", status_code=201)
async def create_message_template(
    data: MessageTemplateIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    from backend.models import MessageTemplate
    if not data.name.strip() or not data.body.strip():
        raise HTTPException(422, "name and body are required")
    t = MessageTemplate(org_id=current.org_id, name=data.name.strip(),
                        channel=data.channel or "any", body=data.body)
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return {"id": t.id, "name": t.name, "channel": t.channel, "body": t.body}


@router.delete("/message-templates/all/{template_id}")
async def delete_message_template(
    template_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    from backend.models import MessageTemplate
    t = (await db.execute(select(MessageTemplate).where(
        MessageTemplate.id == template_id, MessageTemplate.org_id == current.org_id))).scalar_one_or_none()
    if t:
        await db.delete(t)
        await db.commit()
    return {"ok": True}
