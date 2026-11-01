"""WS-C #12 — structured contact addresses.

Contacts keep their single `address` line (documents, exports, the v1 API and anything older
read it). When any structured part is set, the line is rebuilt from the parts so the two never
disagree; a contact with no parts keeps whatever line it had, untouched.

Postcodes are checked for the countries whose format is well defined; anything else is accepted
as typed (trimmed, max 20 characters).
"""
import re
from typing import Optional

from fastapi import HTTPException

PARTS = ("addr_street", "addr_city", "addr_region", "addr_postcode", "addr_country")
LIMITS = {"addr_street": 200, "addr_city": 120, "addr_region": 120, "addr_postcode": 20, "addr_country": 2}

POSTCODE = {
    "US": (r"\d{5}(-\d{4})?", "5 digits, like 94107 or 94107-1234"),
    "CA": (r"[ABCEGHJ-NPRSTVXY]\d[ABCEGHJ-NPRSTV-Z] ?\d[ABCEGHJ-NPRSTV-Z]\d", "like K1A 0B1"),
    "GB": (r"[A-Z]{1,2}\d[A-Z\d]? ?\d[A-Z]{2}", "like SW1A 1AA"),
    "IE": (r"[A-Z]\d[\dW] ?[A-Z\d]{4}", "like D02 X285"),
    "IN": (r"[1-9]\d{5}", "6 digits, like 560001"),
    "AU": (r"\d{4}", "4 digits, like 2000"),
    "NZ": (r"\d{4}", "4 digits"),
    "DE": (r"\d{5}", "5 digits"),
    "FR": (r"\d{5}", "5 digits"),
    "ES": (r"\d{5}", "5 digits"),
    "IT": (r"\d{5}", "5 digits"),
    "NL": (r"\d{4} ?[A-Z]{2}", "like 1012 AB"),
    "BE": (r"\d{4}", "4 digits"),
    "CH": (r"\d{4}", "4 digits"),
    "AT": (r"\d{4}", "4 digits"),
    "SE": (r"\d{3} ?\d{2}", "like 114 55"),
    "PL": (r"\d{2}-\d{3}", "like 00-950"),
    "PT": (r"\d{4}-\d{3}", "like 1000-001"),
    "SG": (r"\d{6}", "6 digits"),
    "AE": (r".{0,20}", ""),
    "ZA": (r"\d{4}", "4 digits"),
    "BR": (r"\d{5}-?\d{3}", "like 01310-100"),
    "MX": (r"\d{5}", "5 digits"),
    "JP": (r"\d{3}-?\d{4}", "like 100-0001"),
}


def _clean(v, n: int) -> Optional[str]:
    v = (str(v).strip() if v is not None else "")
    return v[:n] or None


def postcode_problem(country: Optional[str], postcode: Optional[str]) -> Optional[str]:
    if not postcode or not country or country not in POSTCODE:
        return None
    pattern, hint = POSTCODE[country]
    if re.fullmatch(pattern, postcode.strip().upper()):
        return None
    return f"That postcode doesn't look right for {country}" + (f" ({hint})" if hint else "")


def one_line(street=None, city=None, region=None, postcode=None, country=None) -> Optional[str]:
    """Street, City, Region Postcode, Country — skipping empty parts."""
    tail = " ".join(p for p in (region, postcode) if p)
    line = ", ".join(p for p in (street, city, tail, country) if p)
    return line or None


def apply(obj, payload: dict) -> None:
    """Set structured parts from `payload` (only keys present), validate, and rebuild the
    single line when any part is set. Mutates obj; raises 422 on a bad country or postcode."""
    touched = False
    for k in PARTS:
        if k in payload:
            v = _clean(payload[k], 3 if k == "addr_country" else LIMITS[k])   # 3: so "USA" is caught, not cut
            if k == "addr_country" and v:
                v = v.upper()
                if not re.fullmatch(r"[A-Z]{2}", v):
                    raise HTTPException(422, "Country must be a 2-letter code, like US or GB")
            if k == "addr_postcode" and v:
                v = v.upper()
            setattr(obj, k, v)
            touched = True
    if not touched:
        return
    problem = postcode_problem(getattr(obj, "addr_country", None), getattr(obj, "addr_postcode", None))
    if problem:
        raise HTTPException(422, problem)
    parts = [getattr(obj, k, None) for k in PARTS]
    obj.address = one_line(*parts)        # clearing every part clears the line too


def typed_line(obj, line) -> None:
    """Someone typed the single line directly (older screens, imports, v1): it no longer matches
    the parts, so drop them rather than let a later part edit overwrite what was typed."""
    if (line or None) != (getattr(obj, "address", None) or None):
        for k in PARTS:
            setattr(obj, k, None)


def ser(obj) -> dict:
    return {"street": obj.addr_street, "city": obj.addr_city, "region": obj.addr_region,
            "postcode": obj.addr_postcode, "country": obj.addr_country}
