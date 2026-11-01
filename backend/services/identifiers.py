"""Asset identifiers (IMEI / VIN / HIN / serial): one place to tidy, sanity-check and match them.

Staff type or scan these at the counter. Stored as typed, "1hgcm82633a004352" never matched a
scan of "1HGCM82633A004352", and a phone could be on file twice. Everything that saves or looks
up an identifier goes through here:

- ``normalize`` — IMEI keeps digits only; VIN / HIN are upper-cased with spaces and dashes
  removed (they never contain either); serials are upper-cased with spaces removed but keep
  their dashes, which some manufacturers print as part of the serial.
- ``problem`` — a friendly warning when the value can't be right (IMEI checksum, VIN check
  digit, length). Warnings never block saving: the customer is standing at the counter.
- ``guess_kind`` — what a scanned code most likely is, when the caller doesn't say.
"""
import re
from typing import Optional

KINDS = ("imei", "vin", "hin", "serial")
_STRIP = re.compile(r"[\s\-_./]+")


def normalize(kind: str, value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    v = str(value).strip()
    if not v:
        return None
    if kind == "imei":
        digits = re.sub(r"\D", "", v)
        return digits or None
    if kind == "serial":
        return re.sub(r"\s+", "", v).upper() or None
    return _STRIP.sub("", v).upper() or None


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


_VIN_MAP = {**{str(i): i for i in range(10)},
            **dict(zip("ABCDEFGH", range(1, 9))), **dict(zip("JKLMN", range(1, 6))), "P": 7, "R": 9,
            **dict(zip("STUVWXYZ", range(2, 10)))}
_VIN_W = [8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2]


def _vin_check_ok(vin: str) -> bool:
    try:
        s = sum(_VIN_MAP[c] * w for c, w in zip(vin, _VIN_W))
    except KeyError:
        return False
    want = s % 11
    return vin[8] == ("X" if want == 10 else str(want))


def problem(kind: str, value: Optional[str]) -> Optional[str]:
    """A short reason this value looks wrong, or None. Expects a normalized value."""
    if not value:
        return None
    if kind == "imei":
        if len(value) not in (15, 16):
            return f"An IMEI has 15 digits — this has {len(value)}."
        if len(value) == 15 and not _luhn_ok(value):
            return "This IMEI's check digit doesn't match — probably a typo."
    elif kind == "vin":
        if len(value) != 17:
            return f"A VIN has 17 characters — this has {len(value)}."
        if re.search(r"[IOQ]", value):
            return "VINs never contain I, O or Q — check for 1 / 0 mix-ups."
        if not _vin_check_ok(value):
            return "This VIN's check digit doesn't match — probably a typo (non-North-American VINs may differ)."
    elif kind == "hin":
        if len(value) not in (12, 14):
            return f"A HIN has 12 characters — this has {len(value)}."
    return None


def guess_kind(raw: str) -> str:
    v = normalize("serial", raw) or ""
    digits = re.sub(r"\D", "", raw or "")
    if digits == re.sub(r"[\s\-]", "", raw or "") and len(digits) in (15, 16):
        return "imei"
    if len(v) == 17 and re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", v):
        return "vin"
    if len(v) in (12, 14) and re.fullmatch(r"[A-Z]{3}[A-Z0-9]{9}([A-Z0-9]{2})?", v) and not v.isdigit():
        return "hin"
    return "serial"


def tidy_device(d) -> None:
    """Normalize every identifier column on a Device in place."""
    for k in KINDS:
        setattr(d, k, normalize(k, getattr(d, k, None)))
