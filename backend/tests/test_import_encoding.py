"""Hardening council M14: Excel for Mac's default "Comma Separated Values (.csv)" (not "CSV UTF-8")
writes Mac Roman. services/import_cells._decode tried utf-8-sig then cp1252; Mac Roman bytes
usually decode "fine" as cp1252, so names were silently corrupted (é → Ž, ü → Ÿ) and saved that
way, or refused outright when a byte cp1252 doesn't define (ç, è, ù) was in the file.

Now the non-UTF-8 guess is scored (Ž Ÿ ‚ ƒ „ † ‡ ˆ ‰ Š … in a decoding are a strong sign of the
wrong one), latin-1 is the last resort, and the parse response says which encoding was used.
"""
import io

import pytest
from fastapi import HTTPException

from backend.services import import_cells as C

pytestmark = pytest.mark.asyncio


async def _parse(client, h, raw: bytes, name="clients.csv"):
    r = await client.post("/api/imports/parse?kind=clients", headers=h,
                          files={"file": (name, io.BytesIO(raw), "text/csv")})
    assert r.status_code == 200, r.text
    return r.json()


# ── the decoder ──────────────────────────────────────────────────────────────
def test_decode_picks_mac_roman_over_a_corrupting_cp1252_guess():
    raw = "Name,Phone\nJosé Müller,0412000111\nZoë Lançon,0412000222\n".encode("mac_roman")
    text, enc = C.decode_text(raw)
    assert enc == "mac_roman"
    assert "José Müller" in text and "Zoë Lançon" in text


def test_decode_keeps_cp1252_for_a_windows_file():
    raw = "Name,Note\nJosé Müller,“quoted” – 10 € – Straße\nØrsted,ñandú\n".encode("cp1252")
    text, enc = C.decode_text(raw)
    assert enc == "cp1252"
    assert "José Müller" in text and "“quoted” – 10 € – Straße" in text and "Ørsted" in text


def test_decode_utf8_and_bom_are_untouched():
    assert C.decode_text("Name\nJosé\n".encode("utf-8-sig")) == ("Name\nJosé\n", "utf-8")
    assert C.decode_text(b"Name\nPlain\n") == ("Name\nPlain\n", "utf-8")


def test_decode_never_refuses_eight_bit_text():
    # 0x81 / 0x8D / 0x8F / 0x90 / 0x9D are undefined in cp1252: before, such a file was refused.
    raw = b"Name\nJos\x8d \x81\x8f\x90\x9d\n"
    text, enc = C.decode_text(raw)
    assert enc in ("mac_roman", "latin-1") and text.startswith("Name\nJos")


# ── the API ──────────────────────────────────────────────────────────────────
async def test_mac_roman_csv_names_survive_parse(client, tenant):
    raw = "Name,Phone\nJosé Müller,0412000111\nZoë Lançon,0412000222\n".encode("mac_roman")
    p = await _parse(client, tenant, raw)
    assert [row[0] for row in p["rows"]] == ["José Müller", "Zoë Lançon"]
    assert p["encoding"] == "mac_roman"
    assert "Mac Roman" in p["encoding_note"] and "accents" in p["encoding_note"]


async def test_mac_roman_csv_names_survive_commit(client, tenant):
    raw = "Name,Phone\nJosé Müller,0412000111\n".encode("mac_roman")
    p = await _parse(client, tenant, raw)
    r = await client.post("/api/imports/clients/commit", headers=tenant, json={
        "headers": p["headers"], "rows": p["rows"], "row_numbers": p["row_numbers"],
        "mapping": {"Name": "name", "Phone": "phone"}, "file_name": "clients.csv"})
    assert r.status_code == 200, r.text
    assert r.json()["created"] == 1
    got = (await client.get("/api/clients?q=0412000111", headers=tenant)).json()
    items = got.get("items", got)
    assert [c["name"] for c in items] == ["José Müller"]


async def test_utf8_csv_reports_no_note(client, tenant):
    p = await _parse(client, tenant, "Name,Phone\nJosé Müller,0412000111\n".encode())
    assert [row[0] for row in p["rows"]] == ["José Müller"]
    assert p["encoding"] == "utf-8" and p["encoding_note"] is None


async def test_windows_csv_still_reads(client, tenant):
    p = await _parse(client, tenant, "Name,Phone\nJosé Müller,0412000111\n".encode("cp1252"))
    assert [row[0] for row in p["rows"]] == ["José Müller"]
    assert p["encoding"] == "cp1252" and p["encoding_note"]


def test_binary_is_still_refused_as_not_a_csv():
    with pytest.raises(HTTPException) as e:
        C.read_sheet(b"Name\n\x00\x00\x01\n", "x.csv")
    assert e.value.status_code == 422
