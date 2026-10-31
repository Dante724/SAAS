"""Build 76 — Product ↔ Asset conversion.

A **Product** is shop-owned catalog/inventory. An **Asset** (Device) is a physical
item being tracked/serviced, owned by a customer, supplier, or the company. They
are deliberately *not* merged: conversion moves an item between the two worlds and
writes an **immutable conversion document** recording exactly what happened.

Rules (Orderry):
  * Product → Asset only for **serialized** products, and only for a specific stock
    unit with a unique serial — the serial is what proves the physical item matches
    inventory.
  * A unit sitting on an **open work order** cannot be converted; close the order or
    remove the line first.
  * Converting a company-owned unit **removes it from available inventory**.
  * Conversion documents are never deleted. Reversal = the opposite conversion,
    which writes a second document and links the two.
"""
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import (
    AssetProductConversion,
    Device,
    Order,
    OrderStatus,
    Part,
    SerializedItem,
)

OPEN_UNIT_STATUSES = ("allocated_to_order",)


async def _is_on_open_order(db: AsyncSession, org_id: str, unit: SerializedItem) -> bool:
    """True when the unit is attached to a work order that isn't closed/archived."""
    if not unit.order_id:
        return False
    order = (await db.execute(select(Order).where(Order.id == unit.order_id))).scalar_one_or_none()
    if not order or order.archived:
        return False
    terminal = (await db.execute(select(OrderStatus.is_terminal).where(
        OrderStatus.org_id == org_id, OrderStatus.key == order.status))).scalars().first()
    return not bool(terminal)


async def prefill_product_to_asset(db: AsyncSession, org_id: str, unit_id: str) -> dict:
    """Everything the convert-to-asset form should show, pre-filled from the unit."""
    unit, part = await _load_unit(db, org_id, unit_id)
    return {
        "serialized_item_id": unit.id,
        "part_id": part.id,
        "product_name": part.name,
        "owner_type": "company",                 # a stock unit is shop-owned
        "template_key": part.template_key,
        "asset_group_id": part.asset_group_id,
        "brand": part.asset_brand or part.brand,
        "model": part.asset_model or part.name,
        "serial": unit.serial_number,
        "condition": "used",
        "location_id": unit.location_id,
        "purchase_price": float(unit.unit_cost or 0),
        "image_data": part.image_data,
    }


async def _load_unit(db: AsyncSession, org_id: str, unit_id: str):
    unit = (await db.execute(select(SerializedItem).where(
        SerializedItem.id == unit_id, SerializedItem.org_id == org_id))).scalar_one_or_none()
    if not unit:
        raise HTTPException(404, "Stock unit not found")
    part = (await db.execute(select(Part).where(Part.id == unit.part_id))).scalar_one_or_none()
    if not part:
        raise HTTPException(404, "Product not found")
    return unit, part


async def product_to_asset(db: AsyncSession, org_id: str, employee_id: str, *,
                           unit_id: str, owner_type: str = "company",
                           client_id: Optional[str] = None,
                           template_key: Optional[str] = None,
                           asset_group_id: Optional[str] = None,
                           brand: Optional[str] = None, model: Optional[str] = None,
                           condition: Optional[str] = None,
                           custom_data: Optional[dict] = None,
                           notes: Optional[str] = None) -> tuple[Device, AssetProductConversion]:
    unit, part = await _load_unit(db, org_id, unit_id)

    # ── guards ────────────────────────────────────────────────────────────────
    if not part.serialized:
        raise HTTPException(
            400, "Only serialized products can become assets — a unique serial number is "
                 "what proves the physical item matches inventory.")
    if not unit.serial_number:
        raise HTTPException(400, "This stock unit has no serial number")
    if unit.converted_asset_id:
        raise HTTPException(409, "This unit has already been converted to an asset")
    if unit.status in ("sold", "written_off"):
        raise HTTPException(409, f"This unit is {unit.status} and can't be converted")
    if await _is_on_open_order(db, org_id, unit):
        raise HTTPException(
            409, "This product is on an open work order. Close the order or remove the "
                 "product from it before converting.")
    if owner_type == "client" and not client_id:
        raise HTTPException(400, "A client-owned asset needs client_id")

    # ── create the asset ──────────────────────────────────────────────────────
    ident = unit.serial_number
    tkey = template_key or part.template_key
    device = Device(
        org_id=org_id,
        client_id=client_id if owner_type == "client" else None,
        owner_type=owner_type,
        location_id=unit.location_id,
        asset_status="in_stock" if owner_type == "company" else "in_use",
        purchase_price=float(unit.unit_cost or 0),
        template_key=tkey,
        asset_group_id=asset_group_id or part.asset_group_id,
        brand=brand or part.asset_brand or part.brand,
        model=model or part.asset_model or part.name,
        serial=ident,
        condition=condition,
        custom_data=custom_data or {},
    )
    db.add(device)
    await db.flush()

    # ── inventory effect: a company unit leaves available stock ───────────────
    owner_before = "company"
    if unit.location_id:
        from backend.services import stock_rows
        have, _held = await stock_rows.totals(db, part.id, unit.location_id)
        if have > 0:      # out of the unit's own bin first
            try:
                await stock_rows.draw(db, part.id, unit.location_id, min(1, have), bin_id=unit.bin_id)
            except stock_rows.NotEnoughStock:
                pass      # taken concurrently — never below zero, as before
    unit.status = "converted_to_asset"
    unit.converted_asset_id = device.id

    conv = AssetProductConversion(
        org_id=org_id, direction="product_to_asset", part_id=part.id,
        serialized_item_id=unit.id, device_id=device.id,
        owner_before=owner_before, owner_after=owner_type,
        template_key=tkey, asset_group_id=device.asset_group_id,
        serial_identifier=ident, created_by_id=employee_id,
        metadata_json={
            "product_name": part.name, "sku": part.sku,
            "brand": device.brand, "model": device.model,
            "unit_cost": float(unit.unit_cost or 0), "notes": notes,
        })
    db.add(conv)
    await db.flush()

    # a conversion in the opposite direction closes the loop on an earlier one
    await _link_reversal(db, org_id, conv, ident)
    await db.commit()
    await db.refresh(device)
    await db.refresh(conv)
    return device, conv


async def prefill_asset_to_product(db: AsyncSession, org_id: str, device_id: str) -> dict:
    """Pre-fill the convert-to-product form, and surface likely duplicates so the
    user links an existing product instead of creating a second one."""
    device = await _load_device(db, org_id, device_id)
    name = " ".join(x for x in [device.brand, device.model] if x) or "Converted asset"
    ident = device.serial or device.imei or device.vin or device.hin

    matches = []
    if device.brand or device.model:
        q = select(Part).where(Part.org_id == org_id, Part.active == True)  # noqa: E712
        for p in (await db.execute(q)).scalars().all():
            hay = f"{p.name} {p.brand or ''} {p.asset_brand or ''} {p.asset_model or ''}".lower()
            if device.model and device.model.lower() in hay:
                matches.append({"id": p.id, "name": p.name, "sku": p.sku,
                                "sell_price": p.sell_price, "serialized": p.serialized})
    return {
        "device_id": device.id,
        "product_name": name,
        "template_key": device.template_key,
        "asset_group_id": device.asset_group_id,
        "brand": device.brand, "model": device.model,
        "serial_identifier": ident,
        # the serial must NOT silently become the SKU — the user opts in
        "suggested_sku": None,
        "cost_price": float(device.purchase_price or 0),
        "sell_price": float(device.sale_price or 0),
        "owner_type": device.owner_type,
        "possible_duplicates": matches[:5],
    }


async def _load_device(db: AsyncSession, org_id: str, device_id: str) -> Device:
    from backend.models import Client
    device = (await db.execute(select(Device).where(Device.id == device_id))).scalar_one_or_none()
    if not device:
        raise HTTPException(404, "Asset not found")
    # tenant check: assets carry org_id; rows not yet backfilled fall back to the owning
    # client's org. A row with neither can't be proven ours, so it is not found.
    owner_org = device.org_id
    if not owner_org and device.client_id:
        owner_org = (await db.execute(select(Client.org_id).where(
            Client.id == device.client_id))).scalar_one_or_none()
    if owner_org != org_id:
        raise HTTPException(404, "Asset not found")
    return device


async def asset_to_product(db: AsyncSession, org_id: str, employee_id: str, *,
                           device_id: str, part_id: Optional[str] = None,
                           name: Optional[str] = None, sku: Optional[str] = None,
                           category: Optional[str] = None,
                           cost_price: float = 0, sell_price: float = 0,
                           create_stock_unit: bool = False,
                           location_id: Optional[str] = None,
                           notes: Optional[str] = None) -> tuple[Part, AssetProductConversion]:
    device = await _load_device(db, org_id, device_id)
    if device.converted_to_part_id:
        raise HTTPException(409, "This asset has already been converted to a product")

    # open work order? the asset is being serviced — don't sell it out from under the job
    open_order = (await db.execute(select(Order).where(
        Order.org_id == org_id, Order.device_id == device.id,
        Order.archived == False))).scalars().all()  # noqa: E712
    for o in open_order:
        terminal = (await db.execute(select(OrderStatus.is_terminal).where(
            OrderStatus.org_id == org_id, OrderStatus.key == o.status))).scalars().first()
        if not terminal:
            raise HTTPException(
                409, f"This asset is on open work order {o.order_number}. Close it first.")

    ident = device.serial or device.imei or device.vin or device.hin
    owner_before = device.owner_type or "client"

    # Build 117: snapshot the asset's complete service history into the conversion's audit
    # trail so nothing is lost when it becomes a product. The device row itself is kept
    # (converted_to_part_id links them), so the orders/notes/attachments remain reachable;
    # this snapshot records what existed at conversion time for a self-documenting trail.
    hist_orders = (await db.execute(select(Order).where(
        Order.org_id == org_id, Order.device_id == device.id))).scalars().all()
    history_snapshot = {
        "work_orders": [{"id": o.id, "order_number": o.order_number, "status": o.status}
                        for o in hist_orders],
        "work_order_count": len(hist_orders),
        "owner_client_id": device.client_id,
        "serial": device.serial, "imei": device.imei,
        "vin": device.vin, "hin": device.hin,
        "purchase_price": float(device.purchase_price or 0),
        "device_notes": getattr(device, "notes", None),
    }

    # link an existing product (dedupe) or create a new one
    if part_id:
        part = (await db.execute(select(Part).where(
            Part.id == part_id, Part.org_id == org_id))).scalar_one_or_none()
        if not part:
            raise HTTPException(404, "Product to link not found")
        created = False
    else:
        part = Part(
            org_id=org_id,
            name=name or " ".join(x for x in [device.brand, device.model] if x) or "Converted asset",
            sku=sku,                      # never auto-filled from the serial
            brand=device.brand,
            category=category,
            cost_price=cost_price or float(device.purchase_price or 0),
            sell_price=sell_price or float(device.sale_price or 0),
            serialized=True,              # it has a unique identity
            template_key=device.template_key,
            asset_group_id=device.asset_group_id,
            asset_brand=device.brand,
            asset_model=device.model,
            compatibility_json={"from_asset": device.id},
        )
        db.add(part)
        await db.flush()
        created = True

    # optionally bring it into stock as a serialized unit (company-owned inventory)
    unit = None
    if create_stock_unit and ident:
        unit = SerializedItem(
            org_id=org_id, part_id=part.id, serial_number=ident,
            location_id=location_id or device.location_id,
            unit_cost=cost_price or float(device.purchase_price or 0),
            status="in_stock")
        db.add(unit)
        await db.flush()
        loc = unit.location_id
        if loc:
            from backend.services import stock_rows
            await stock_rows.add(db, part.id, loc, 1, bin_id=unit.bin_id)

    device.converted_to_part_id = part.id
    device.asset_status = "for_sale" if create_stock_unit else device.asset_status

    conv = AssetProductConversion(
        org_id=org_id, direction="asset_to_product", part_id=part.id,
        serialized_item_id=unit.id if unit else None, device_id=device.id,
        owner_before=owner_before, owner_after="company",
        template_key=device.template_key, asset_group_id=device.asset_group_id,
        serial_identifier=ident, created_by_id=employee_id,
        metadata_json={
            "product_name": part.name, "linked_existing": not created,
            "stock_unit_created": bool(unit), "notes": notes,
            "history": history_snapshot,          # Build 117: full service history preserved
        })
    db.add(conv)
    await db.flush()
    await _link_reversal(db, org_id, conv, ident)
    await db.commit()
    await db.refresh(part)
    await db.refresh(conv)
    return part, conv


async def _link_reversal(db: AsyncSession, org_id: str, conv: AssetProductConversion,
                         ident: Optional[str]) -> None:
    """If an earlier conversion in the opposite direction exists for the same serial,
    mark it reversed and point the two documents at each other. Neither document is
    deleted — the pair *is* the audit trail."""
    if not ident:
        return
    opposite = ("asset_to_product" if conv.direction == "product_to_asset"
                else "product_to_asset")
    prior = (await db.execute(select(AssetProductConversion).where(
        AssetProductConversion.org_id == org_id,
        AssetProductConversion.direction == opposite,
        AssetProductConversion.serial_identifier == ident,
        AssetProductConversion.status == "completed",
        AssetProductConversion.id != conv.id)
        .order_by(AssetProductConversion.created_at.desc()))).scalars().first()
    if prior:
        prior.status = "reversed"
        prior.reversed_by_id = conv.id
        md = dict(conv.metadata_json or {})
        md["reverses"] = prior.id
        conv.metadata_json = md


def serialize(c: AssetProductConversion) -> dict:
    return {
        "id": c.id, "direction": c.direction, "part_id": c.part_id,
        "serialized_item_id": c.serialized_item_id, "device_id": c.device_id,
        "owner_before": c.owner_before, "owner_after": c.owner_after,
        "template_key": c.template_key, "asset_group_id": c.asset_group_id,
        "serial_identifier": c.serial_identifier, "status": c.status,
        "reversed_by_id": c.reversed_by_id, "created_by_id": c.created_by_id,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "metadata": c.metadata_json or {},
    }


async def _atomic_add(db, obj, attr, delta, *, round_to=None):
    from backend.services.money import add_to_column
    return await add_to_column(db, obj, attr, delta, round_to=round_to)
