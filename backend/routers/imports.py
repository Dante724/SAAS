"""Import centre API — one Import button per list (Products, Services, Clients, Assets, Suppliers).

    GET  /api/imports/{kind}/template?fmt=xlsx|csv   the fill-in template
    POST /api/imports/parse?kind=…&supplier_id=…     read an XLSX/CSV → headers, rows, suggested mapping
    POST /api/imports/{kind}/preview                 dry run: a verdict per row, writes nothing
    POST /api/imports/{kind}/commit                  save the good rows, skip the red ones
    GET  /api/imports/batches?kind=…                 recent imports (undo banner)
    POST /api/imports/batches/{id}/undo              roll back the last import of a list (24 h)
    GET/PUT /api/imports/{kind}/mapping              remembered column mapping (per supplier for posting/po)

The rules themselves live in services/importer.py; cell reading in services/import_cells.py.
"""
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.spreadsheet import MAX_IMPORT_ROWS, MAX_XLSX_BYTES
from backend.db.database import get_db
from backend.models import Employee, ImportBatch, ImportMapping, Supplier
from backend.services import importer
from backend.services.auth import get_current_employee, has_perm
from backend.services.import_cells import read_sheet

router = APIRouter(prefix="/api/imports", tags=["imports"])


def _kind(kind: str) -> str:
    if kind not in importer.KINDS:
        raise HTTPException(404, "Unknown list")
    return kind


async def _supplier(db, org_id: str, supplier_id: Optional[str]) -> Optional[str]:
    if not supplier_id:
        return None
    ok = (await db.execute(select(Supplier.id).where(
        Supplier.id == supplier_id, Supplier.org_id == org_id))).scalar_one_or_none()
    if not ok:
        raise HTTPException(404, "Supplier not found")
    return supplier_id


async def _mapping_row(db, org_id: str, kind: str, supplier_id: Optional[str]):
    q = select(ImportMapping).where(ImportMapping.org_id == org_id, ImportMapping.kind == kind)
    q = q.where(ImportMapping.supplier_id == supplier_id) if supplier_id else q.where(ImportMapping.supplier_id.is_(None))
    return (await db.execute(q.order_by(ImportMapping.updated_at.desc()).limit(1))).scalar_one_or_none()


@router.get("/kinds")
async def kinds(db: AsyncSession = Depends(get_db), current: Employee = Depends(get_current_employee)):
    """Columns of every list, for the column-matching step."""
    out = {}
    for key, k in importer.KINDS.items():
        out[key] = {"noun": k.noun, "match_on": k.match_help,
                    "fields": [{"key": f.key, "label": f.label, "required": f.required, "type": f.type,
                                "cost": f.cost, "help": f.help} for f in k.fields]}
    out["posting"] = {"noun": "line", "match_on": "", "fields": [
        {"key": f.key, "label": f.label, "required": False, "type": f.type, "cost": False, "help": ""}
        for f in importer.POSTING_FIELDS]}
    return out


@router.get("/{kind}/template")
async def template(kind: str, fmt: str = Query("xlsx", pattern="^(xlsx|csv)$"),
                   db: AsyncSession = Depends(get_db), current: Employee = Depends(get_current_employee)):
    _kind(kind)
    await importer.assert_access(db, current, kind)
    body, mime, name = await importer.template(db, current.org_id, kind, fmt)
    return Response(body, media_type=mime, headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.post("/parse")
async def parse(kind: str = Query(...), supplier_id: Optional[str] = None, file: UploadFile = File(...),
                db: AsyncSession = Depends(get_db), current: Employee = Depends(get_current_employee)):
    if kind not in importer.MAPPING_KINDS:
        raise HTTPException(404, "Unknown list")
    await importer.assert_access(db, current, kind)
    supplier_id = await _supplier(db, current.org_id, supplier_id)
    raw = await file.read(MAX_XLSX_BYTES + 1)
    headers, rows, numbers = read_sheet(raw, file.filename or "")
    remembered = await _mapping_row(db, current.org_id, kind, supplier_id)
    if remembered is None and supplier_id:
        remembered = await _mapping_row(db, current.org_id, kind, None)
    return {"file_name": (file.filename or "")[:255], "headers": headers, "rows": rows, "row_numbers": numbers,
            "total": len(rows),
            "suggested": importer.suggest_mapping(kind, headers, remembered.mapping if remembered else None)}


class RunIn(BaseModel):
    headers: list[str] = Field(max_length=500)
    rows: list[list[Any]] = Field(max_length=MAX_IMPORT_ROWS)
    row_numbers: Optional[list[int]] = Field(None, max_length=MAX_IMPORT_ROWS)
    mapping: dict[str, str] = {}
    options: dict[str, Any] = {}
    file_name: Optional[str] = Field(None, max_length=255)
    remember: bool = True        # save this mapping as the list's default on commit


@router.post("/{kind}/preview")
async def preview(kind: str, data: RunIn, db: AsyncSession = Depends(get_db),
                  current: Employee = Depends(get_current_employee)):
    return await importer.run(db, current, _kind(kind), data.headers, data.rows, data.mapping, data.options,
                              commit=False, file_name=data.file_name, row_numbers=data.row_numbers)


@router.post("/{kind}/commit")
async def commit(kind: str, data: RunIn, db: AsyncSession = Depends(get_db),
                 current: Employee = Depends(get_current_employee)):
    out = await importer.run(db, current, _kind(kind), data.headers, data.rows, data.mapping, data.options,
                             commit=True, file_name=data.file_name, row_numbers=data.row_numbers)
    if data.remember and data.mapping:
        await _save_mapping(db, current.org_id, kind, None, data.mapping)
        await db.commit()
    return out


async def _save_mapping(db, org_id, kind, supplier_id, mapping: dict):
    row = await _mapping_row(db, org_id, kind, supplier_id)
    if row is None:
        db.add(ImportMapping(org_id=org_id, kind=kind, supplier_id=supplier_id, mapping=mapping))
    else:
        row.mapping = mapping


class MappingIn(BaseModel):
    mapping: dict[str, str] = Field(max_length=500)


@router.get("/{kind}/mapping")
async def get_mapping(kind: str, supplier_id: Optional[str] = None, db: AsyncSession = Depends(get_db),
                      current: Employee = Depends(get_current_employee)):
    if kind not in importer.MAPPING_KINDS:
        raise HTTPException(404, "Unknown list")
    await importer.assert_access(db, current, kind)
    supplier_id = await _supplier(db, current.org_id, supplier_id)
    row = await _mapping_row(db, current.org_id, kind, supplier_id)
    return {"mapping": row.mapping if row else None}


@router.put("/{kind}/mapping")
async def put_mapping(kind: str, data: MappingIn, supplier_id: Optional[str] = None,
                      db: AsyncSession = Depends(get_db), current: Employee = Depends(get_current_employee)):
    if kind not in importer.MAPPING_KINDS:
        raise HTTPException(404, "Unknown list")
    await importer.assert_access(db, current, kind)
    supplier_id = await _supplier(db, current.org_id, supplier_id)
    keys = {f.key for f in importer.fields_for(kind)} | {"ignore"}
    if set(data.mapping.values()) - keys:
        raise HTTPException(422, "Unknown field in mapping")
    clean = {str(h)[:200]: f for h, f in data.mapping.items()}
    await _save_mapping(db, current.org_id, kind, supplier_id, clean)
    await db.commit()
    return {"ok": True, "mapping": clean}


async def _ser_batch(db, b: ImportBatch, latest: Optional[str]) -> dict:
    actor = await db.get(Employee, b.actor_id) if b.actor_id else None
    return {"id": b.id, "kind": b.kind, "file_name": b.file_name, "created": b.created_count,
            "updated": b.updated_count, "failed": b.failed_count, "created_at": b.created_at.isoformat(),
            "undone_at": b.undone_at.isoformat() if b.undone_at else None,
            "actor_name": actor.name if actor else None,
            "undo_until": (b.created_at + importer.UNDO_WINDOW).isoformat(),
            "can_undo": importer.can_undo(b, latest)}


@router.get("/batches")
async def batches(kind: str = Query(...), db: AsyncSession = Depends(get_db),
                  current: Employee = Depends(get_current_employee)):
    _kind(kind)
    await importer.assert_access(db, current, kind)
    rows = (await db.execute(select(ImportBatch).where(ImportBatch.org_id == current.org_id, ImportBatch.kind == kind)
                             .order_by(ImportBatch.created_at.desc(), ImportBatch.id.desc()).limit(10))).scalars().all()
    latest = rows[0].id if rows else None
    return {"items": [await _ser_batch(db, b, latest) for b in rows]}


@router.post("/batches/{batch_id}/undo")
async def undo(batch_id: str, db: AsyncSession = Depends(get_db), current: Employee = Depends(get_current_employee)):
    b = (await db.execute(select(ImportBatch).where(
        ImportBatch.id == batch_id, ImportBatch.org_id == current.org_id))).scalar_one_or_none()
    if b is None:
        raise HTTPException(404, "Import not found")
    await importer.assert_access(db, current, b.kind)
    if b.actor_id != current.id and not await has_perm(db, current, "can_manage_settings"):
        raise HTTPException(403, "Only the person who ran this import or a manager can undo it")
    return await importer.undo(db, current, b)
