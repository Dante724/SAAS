"""Where a catalog item (part/service) is used — bundles (job kits). Powers delete guards and
'where used' so deleting a product/service can't silently orphan a job kit. (144co; 144df: the
legacy JSON JobKit was retired — bundles are the one job-kit engine now.)"""
from sqlalchemy import select
from backend.models import Bundle, BundleComponent


async def bundles_using(db, org_id, ref_type, ref_id):
    rows = (await db.execute(
        select(Bundle).join(BundleComponent, BundleComponent.bundle_id == Bundle.id)
        .where(Bundle.org_id == org_id, Bundle.active == True,  # noqa: E712
               BundleComponent.ref_type == ref_type, BundleComponent.ref_id == ref_id)
    )).scalars().unique().all()
    return [{"id": b.id, "name": b.name, "where": "job kit"} for b in rows]


async def catalog_usage(db, org_id, ref_type, ref_id):
    """All job kits (bundles) that reference this part/service. Empty = safe to delete."""
    return await bundles_using(db, org_id, ref_type, ref_id)
