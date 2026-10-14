"""Identifier lookups — VIN/HIN/IMEI structural validation and decoding.

Three identifier kinds, each with *local structural* validation/decoding that
always works, plus a live enrichment seam gated like every provider:
  * VIN  — ISO 3779 check-digit validation + model-year/WMI decode; live
           enrichment via the free, keyless NHTSA vPIC API when reachable.
  * HIN  — USCG 33 CFR 181 format parse (MIC, hull serial, certification
           month/year, model year). Manufacturer name needs the USCG MIC
           database; a small sample map is embedded, the rest report the MIC.
  * IMEI — Luhn validation + TAC/serial split; TAC->model resolution requires
           a commercial provider (IMEI_LOOKUP_API_KEY) and is simulated absent.

The industry-template registry (asset naming, groups, fields, scan rules) now
lives in backend/services/industry.py — this module only decodes identifiers.
"""
import logging

from backend.config import settings

log = logging.getLogger("repairos.lookups")


# ── VIN ──────────────────────────────────────────────────────────────────────
_VIN_TRANS = {**{c: v for c, v in zip("ABCDEFGH", range(1, 9))},
              **{c: v for c, v in zip("JKLMNPR", [1, 2, 3, 4, 5, 7, 9])},
              **{c: v for c, v in zip("STUVWXYZ", [2, 3, 4, 5, 6, 7, 8, 9])},
              **{str(d): d for d in range(10)}}
_VIN_WEIGHTS = [8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2]
_VIN_YEARS = "ABCDEFGHJKLMNPRSTVWXY123456789"   # 1980.. cycle of 30
_WMI_REGION = {"1": "North America", "2": "North America", "3": "North America",
               "4": "North America", "5": "North America", "6": "Oceania", "7": "Oceania",
               "8": "South America", "9": "South America", "J": "Asia", "K": "Asia",
               "L": "Asia", "M": "Asia", "N": "Asia", "P": "Asia", "R": "Asia",
               "S": "Europe", "T": "Europe", "U": "Europe", "V": "Europe",
               "W": "Europe", "X": "Europe", "Y": "Europe", "Z": "Europe"}


def _vin_check_digit(vin: str) -> str:
    total = sum(_VIN_TRANS[c] * w for c, w in zip(vin, _VIN_WEIGHTS))
    r = total % 11
    return "X" if r == 10 else str(r)


def decode_vin_structural(vin: str) -> dict:
    vin = (vin or "").strip().upper()
    out = {"identifier": vin, "kind": "vin", "valid": False, "source": "structural"}
    if len(vin) != 17 or any(c in "IOQ" for c in vin) or not vin.isalnum():
        out["error"] = "VIN must be 17 characters (letters/digits, no I/O/Q)."
        return out
    check_ok = _vin_check_digit(vin) == vin[8]
    year_char = vin[9]
    year = None
    if year_char in _VIN_YEARS:
        base = 1980 + _VIN_YEARS.index(year_char)
        year = base + 30 if base + 30 <= 2039 and base < 2010 else base  # prefer modern cycle
    out.update({
        "valid": check_ok, "check_digit_ok": check_ok,
        "wmi": vin[:3], "region": _WMI_REGION.get(vin[0]),
        "model_year": year, "vds": vin[3:9], "serial": vin[11:],
    })
    if not check_ok:
        out["error"] = "VIN check digit does not match."
    return out


async def decode_vin(vin: str) -> dict:
    """Structural decode always; NHTSA vPIC enrichment best-effort (keyless)."""
    out = decode_vin_structural(vin)
    if not settings.VIN_LOOKUP_LIVE or len((vin or "").strip()) != 17:
        return out
    try:
        import httpx
        async with httpx.AsyncClient(timeout=6) as c:
            r = await c.get(f"https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValues/{vin.strip().upper()}",
                            params={"format": "json"})
            row = (r.json().get("Results") or [{}])[0]
        if row.get("Make"):
            out.update({"make": row.get("Make"), "model": row.get("Model") or None,
                        "model_year": int(row["ModelYear"]) if row.get("ModelYear") else out.get("model_year"),
                        "vehicle_type": row.get("VehicleType") or None,
                        "valid": out["valid"] or bool(row.get("Make")),
                        "source": "nhtsa"})
    except Exception as e:  # offline / blocked / provider down -> structural only
        log.info("VIN live enrichment unavailable (%s); returning structural decode", type(e).__name__)
    return out


# ── HIN ──────────────────────────────────────────────────────────────────────
_HIN_MONTHS = "ABCDEFGHIJKL"
_MIC_SAMPLE = {"BWC": "Boston Whaler", "SER": "Sea Ray", "YAM": "Yamaha Motor"}  # sample; full list = USCG MIC db


def decode_hin(hin: str) -> dict:
    hin = (hin or "").strip().upper()
    out = {"identifier": hin, "kind": "hin", "valid": False, "source": "structural"}
    if len(hin) != 12 or not hin.isalnum():
        out["error"] = "HIN must be 12 characters (e.g. BWCE1234C404)."
        return out
    mic, serial, m, y, my = hin[:3], hin[3:8], hin[8], hin[9], hin[10:]
    if not (mic.isalpha() and m in _HIN_MONTHS and y.isdigit() and my.isdigit()):
        out["error"] = "HIN format: 3-letter MIC + 5-char hull serial + cert month letter + cert year digit + 2-digit model year."
        return out
    my_i = int(my)
    model_year = 2000 + my_i if my_i <= 39 else 1900 + my_i
    cert_year = model_year - (1 if int(y) > my_i % 10 else 0)  # cert year shares decade with model year
    out.update({
        "valid": True, "mic": mic,
        "manufacturer": _MIC_SAMPLE.get(mic),
        "manufacturer_note": None if mic in _MIC_SAMPLE else "MIC not in embedded sample — resolve via the USCG MIC database.",
        "hull_serial": serial,
        "cert_month": _HIN_MONTHS.index(m) + 1,
        "cert_year_digit": int(y), "cert_year_estimate": cert_year,
        "model_year": model_year,
    })
    return out


# ── IMEI ─────────────────────────────────────────────────────────────────────
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


async def decode_imei(imei: str) -> dict:
    imei = (imei or "").strip().replace(" ", "")
    out = {"identifier": imei, "kind": "imei", "valid": False, "source": "structural"}
    if len(imei) != 15 or not imei.isdigit():
        out["error"] = "IMEI must be 15 digits."
        return out
    ok = _luhn_ok(imei)
    out.update({"valid": ok, "luhn_ok": ok, "tac": imei[:8], "serial": imei[8:14], "check_digit": imei[14]})
    if not ok:
        out["error"] = "IMEI failed the Luhn check."
        return out
    if settings.IMEI_LOOKUP_API_KEY:
        # Provider seam: with a key a TAC database resolves brand/model here.
        out["source"] = "provider"
    else:
        out["device_note"] = "TAC→model resolution needs an IMEI provider key (IMEI_LOOKUP_API_KEY); structural data only."
        log.info("[imei:simulated] TAC lookup skipped (no provider key)")
    return out
