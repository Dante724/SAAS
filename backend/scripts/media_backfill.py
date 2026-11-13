"""Move binary data still kept inline in the database into the media store (PERF-06).

Moves, a batch at a time with a commit after each batch:
  * work-order / client timeline attachments (event_feed_items.media_data)
  * recommendation photos (recommendations.media_data)
  * blobs kept inline while MEDIA_BACKEND was "db", to local/S3 when that backend is now on

Safe to stop and run again: rows already moved are skipped. Only one payload is in memory at a
time. Run it once after switching MEDIA_BACKEND, and once after deploying the PERF-06 change.

Usage (repo root, venv active, the deployment's DATABASE_URL / MEDIA_* settings in the env):
    python -m backend.scripts.media_backfill --dry-run       # counts only
    python -m backend.scripts.media_backfill                 # move everything
    python -m backend.scripts.media_backfill --org <shop id> --batch 20
"""
import argparse
import asyncio
import sys


def parse(argv):
    p = argparse.ArgumentParser(prog="media_backfill", description=__doc__.splitlines()[0])
    p.add_argument("--batch", type=int, default=50, help="rows per transaction (default 50)")
    p.add_argument("--org", default=None, help="only this shop id")
    p.add_argument("--dry-run", action="store_true", help="count what would move; change nothing")
    return p.parse_args(argv)


async def run(args) -> dict:
    from backend.db.database import AsyncSessionLocal
    from backend.services import media
    batches = [0]

    def tick():
        batches[0] += 1
        print(f"  batch {batches[0]} done", flush=True)

    async with AsyncSessionLocal() as db:
        out = await media.backfill(db, batch=max(1, args.batch), org_id=args.org,
                                   dry_run=args.dry_run, on_batch=tick)
    print(("would move " if args.dry_run else "moved ") + ", ".join(f"{k}={v}" for k, v in out.items())
          + f" (backend: {media.active_backend()})")
    return out


def main(argv=None) -> int:
    asyncio.run(run(parse(sys.argv[1:] if argv is None else argv)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
