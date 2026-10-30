"""144jn — per-org integration credentials API (multi-tenant provider keys).

Each shop manages its OWN Stripe/Square/SumUp/Twilio/SMTP/Meta keys here. Reads
are masked (secrets never leave the server in cleartext); writes are gated on the
settings permission and always scoped to the caller's org.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import Employee
from backend.services import org_creds
from backend.services.audit import record as audit_record
from backend.services.auth import get_current_employee, require_perm

router = APIRouter(prefix="/api/integrations/credentials", tags=["integrations"])


@router.get("")
async def list_credentials(db: AsyncSession = Depends(get_db),
                           current: Employee = Depends(get_current_employee)):
    """Masked status of every keyed provider for THIS shop."""
    return await org_creds.status(db, current.org_id)


class CredIn(BaseModel):
    provider: str
    data: dict


@router.put("")
async def set_credentials(data: CredIn, db: AsyncSession = Depends(get_db),
                          current: Employee = Depends(require_perm("can_manage_settings"))):
    if data.provider not in org_creds.PROVIDER_FIELDS:
        raise HTTPException(422, f"Unknown provider '{data.provider}'")
    try:
        await org_creds.set_creds(db, current.org_id, data.provider, data.data or {})
    except ValueError as e:   # F4: live payment keys outside production
        raise HTTPException(422, str(e))
    # Audit M9: who changed which keys — field names only, never the values.
    await audit_record(db, org_id=current.org_id, actor_id=current.id, action="integration.credentials.update",
                       entity_type="integration", entity_id=data.provider,
                       diff={"fields": sorted(str(k) for k in (data.data or {}))})
    await db.commit()
    return await org_creds.status(db, current.org_id)


@router.delete("/{provider}")
async def delete_credentials(provider: str, db: AsyncSession = Depends(get_db),
                             current: Employee = Depends(require_perm("can_manage_settings"))):
    await org_creds.delete_creds(db, current.org_id, provider)
    await audit_record(db, org_id=current.org_id, actor_id=current.id, action="integration.credentials.delete",
                       entity_type="integration", entity_id=provider)
    await db.commit()
    return await org_creds.status(db, current.org_id)
