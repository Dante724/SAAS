"""Phone numbers in international (E.164) form, for wa.me links and SMS deep links.

There's no phone library in the stack, so this covers what shops actually store: numbers that
already carry a "+" or "00" prefix, and local numbers (often with a leading 0) in the shop's own
country. Anything we can't place returns None, and the app falls back to the number as stored.
"""
import re
from typing import Optional

# ISO 3166-1 alpha-2 → country calling code (the countries Tekly shops are in, plus neighbours).
DIAL = {
    "IN": "91", "GB": "44", "IE": "353", "US": "1", "CA": "1", "AU": "61", "NZ": "64",
    "AE": "971", "SA": "966", "QA": "974", "KW": "965", "OM": "968", "BH": "973",
    "PK": "92", "BD": "880", "LK": "94", "NP": "977", "SG": "65", "MY": "60", "PH": "63",
    "ID": "62", "TH": "66", "VN": "84", "ZA": "27", "NG": "234", "KE": "254", "EG": "20",
    "DE": "49", "FR": "33", "ES": "34", "IT": "39", "NL": "31", "BE": "32", "PT": "351",
    "SE": "46", "NO": "47", "DK": "45", "FI": "358", "PL": "48", "CH": "41", "AT": "43",
    "TR": "90", "MX": "52", "BR": "55", "AR": "54", "CO": "57", "CL": "56", "JP": "81",
    "KR": "82", "CN": "86", "HK": "852",
}


def to_e164(raw: Optional[str], country: Optional[str]) -> Optional[str]:
    """'+44 7700 900123' → '+447700900123'; '07700 900123' with country GB → '+447700900123'."""
    if not raw:
        return None
    s = raw.strip()
    digits = re.sub(r"\D", "", s)
    if not digits:
        return None
    if s.startswith("+"):
        e = digits
    elif digits.startswith("00"):
        e = digits[2:]
    else:
        code = DIAL.get((country or "").upper())
        if not code:
            return None
        local = digits.lstrip("0")
        # already starts with the country code and is long enough to be international
        e = local if local.startswith(code) and len(local) >= len(code) + 8 else code + local
    return f"+{e}" if 8 <= len(e) <= 15 else None
