"""What a scanned (or typed) code means — one reading for every lookup path (ii144b).

A handheld scanner, a phone camera and a keyboard all send slightly different text for the same
label. Before a code is looked up it is normalised here:

- **AIM symbology prefix** (`]E0`, `]C1`, `]d2`, `]Q3` …) — sent by scanners set to "transmit AIM
  ID". Stripped; the GS1 ones (`]C1`, `]d2`, `]Q3`, `]e0`, `]J1`) mark the data as a GS1 element string.
- **GTIN forms are one product:** UPC-A `036000291452`, EAN-13 `0036000291452`, GTIN-14
  `00036000291452` and a UPC-E that expands to that UPC-A all share one *match key*, the GTIN-14.
  The key is what `product_barcodes.match_key` stores and what uniqueness is enforced on, so the same
  GTIN can't sit on two products just because one was typed as UPC-A and the other as EAN-13.
- **GS1 Application Identifiers** from GS1-128 / GS1 DataMatrix / GS1 QR — in raw form (FNC1 sent as
  the GS character) or human-readable form `(01)…(17)…` — are parsed: (01) GTIN, (10) batch/lot,
  (17) expiry, (21) serial, so one scan can fill product, batch, expiry and serial at intake.

Pure functions, no database: `parse(raw)` for a scan, `match_key(value, kind)` for a stored code.
"""
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

GS = "\x1d"
_AIM = re.compile(r"^\](?P<sym>[A-Za-z])(?P<mod>[0-9A-Za-z])")
_GS1_AIM = {("C", "1"), ("d", "2"), ("Q", "3"), ("e", "0"), ("J", "1")}
# What some keyboard-wedge scanners / apps type for the invisible GS separator.
_GS_SPELLINGS = ("<GS>", "{GS}", "[GS]", "\\x1d", "\\u001d", "^]", "␝")
MAX_SCAN = 500
_INVISIBLE = {"\u200b", "\u200c", "\u200d", "\u2060", "\ufeff", "\u00ad"}


def is_digits(v: str) -> bool:
    """ASCII 0-9 only (str.isdigit also accepts '²', '①', Arabic-Indic and fullwidth digits)."""
    return bool(v) and v.isascii() and v.isdigit()


def gtin_check(body: str) -> str:
    s = sum((3 if i % 2 == 0 else 1) * int(d) for i, d in enumerate(reversed(body)))
    return str((10 - s % 10) % 10)


def valid_gtin(v: str) -> bool:
    return is_digits(v) and len(v) in (8, 12, 13, 14) and gtin_check(v[:-1]) == v[-1]


def upce_to_upca(v: str) -> Optional[str]:
    """An 8-digit UPC-E (number system 0/1, six data digits, check) → its 12-digit UPC-A, or None.
    A 7-digit body gets its check digit computed; a 6-digit one is assumed number system 0."""
    v = (v or "").strip()
    if not is_digits(v):
        return None
    if len(v) == 6:
        v = "0" + v
    if len(v) not in (7, 8) or v[0] not in "01":
        return None
    ns, d, check = v[0], v[1:7], v[7] if len(v) == 8 else None
    last = d[5]
    if last in "012":
        body = f"{ns}{d[0]}{d[1]}{last}0000{d[2]}{d[3]}{d[4]}"
    elif last == "3":
        body = f"{ns}{d[0]}{d[1]}{d[2]}00000{d[3]}{d[4]}"
    elif last == "4":
        body = f"{ns}{d[0]}{d[1]}{d[2]}{d[3]}00000{d[4]}"
    else:
        body = f"{ns}{d[0]}{d[1]}{d[2]}{d[3]}{d[4]}0000{last}"
    upca = body + gtin_check(body)
    if check is not None and check != upca[-1]:
        return None
    return upca


def gtin14(v: str) -> Optional[str]:
    """A valid GTIN-8/12/13/14 → its zero-padded GTIN-14 (the shared match key), else None."""
    return v.zfill(14) if valid_gtin(v) else None


# ── GS1 Application Identifiers ──────────────────────────────────────────────────────────────────
# (AI, fixed data length or None for variable, max length). Variable fields end at GS or the end.
_FIXED2 = {"00": 18, "01": 14, "02": 14, "03": 14, "04": 16, "11": 6, "12": 6, "13": 6, "15": 6, "16": 6,
           "17": 6, "20": 2}
_VAR2 = {"10": 20, "21": 20, "22": 20, "30": 8, "37": 8, "90": 30, **{str(n): 90 for n in range(91, 100)}}
_FIXED3 = {"410": 13, "411": 13, "412": 13, "413": 13, "414": 13, "415": 13, "416": 13, "417": 13, "422": 3,
           "424": 3, "426": 3}
_VAR3 = {"235": 28, "240": 30, "241": 30, "242": 6, "243": 20, "250": 30, "251": 30, "253": 30, "254": 20,
         "255": 25, "400": 30, "401": 30, "402": 17, "403": 30, "420": 20, "421": 12, "423": 15, "425": 15,
         "427": 3, "710": 20, "711": 20, "712": 20, "713": 20, "714": 20, "715": 20}
_FIXED4 = {"7003": 10, "8005": 6, "8018": 18, "7006": 6, "8001": 14, "8017": 18, "7040": 4, "8026": 18,
           **{f"394{n}": 4 for n in range(10)}, **{f"395{n}": 6 for n in range(10)}}
_VAR4 = {"7001": 13, "7002": 30, "7004": 4, "7005": 12, "7007": 12, "7008": 3, "7009": 10, "7010": 2,
         "8002": 20, "8003": 30, "8004": 30, "8006": 18, "8007": 34, "8008": 12, "8009": 50, "8010": 30,
         "8011": 12, "8012": 20, "8013": 25, "8019": 10, "8020": 25, "8200": 70,
         "7011": 10, "7020": 20, "7021": 20, "7022": 20, "7023": 30, "7240": 20, "8030": 90,
         "8110": 70, "8111": 4, "8112": 70, **{f"723{n}": 30 for n in range(10)},
         **{f"43{n:02d}": 70 for n in range(34)}}
_AI_NAMES = {"01": "gtin", "10": "lot", "17": "expiry", "21": "serial", "11": "production_date",
             "15": "best_before", "30": "count", "37": "count", "00": "sscc"}


def _ai_spec(s: str, i: int) -> Optional[tuple[str, Optional[int], int]]:
    """The AI starting at s[i]: (ai, fixed length or None, max length)."""
    two, three, four = s[i:i + 2], s[i:i + 3], s[i:i + 4]
    if two in _FIXED2:
        return two, _FIXED2[two], _FIXED2[two]
    if two in _VAR2:
        return two, None, _VAR2[two]
    if len(four) == 4 and four[:2] in {str(n) for n in range(31, 37)} and is_digits(four[3]):
        return four, 6, 6                       # 310n–369n: measures, fixed 6
    if len(four) == 4 and four[:3] in ("390", "391", "392", "393") and is_digits(four[3]):
        return four, None, 18                   # amounts payable
    if four in _FIXED4:
        return four, _FIXED4[four], _FIXED4[four]
    if four in _VAR4:
        return four, None, _VAR4[four]
    if three in _FIXED3:
        return three, _FIXED3[three], _FIXED3[three]
    if three in _VAR3:
        return three, None, _VAR3[three]
    return None


def parse_gs1(s: str) -> Optional[list[tuple[str, str]]]:
    """Element string (raw with GS separators, or human-readable with brackets) → [(AI, value), …],
    or None when it isn't a well-formed GS1 string."""
    s = (s or "").strip()
    if not s:
        return None
    if s.startswith("("):
        out = []
        for m in re.finditer(r"\((\d{2,4})\)([^(]*)", s):
            spec = _ai_spec(m.group(1), 0)
            if not spec or spec[0] != m.group(1):
                if out and len(m.group(1)) == 4:   # an AI we don't know, after known ones: brackets delimit it
                    spec = (m.group(1), None, 90)
                else:
                    return None
            ai, fixed, mx = spec
            val = m.group(2).strip()
            if (fixed is not None and len(val) != fixed) or len(val) > mx or not val:
                return None
            if any(a == ai for a, _ in out):
                return None                       # the same AI twice: not a valid GS1 string
            out.append((ai, val))
        return out if out and re.fullmatch(r"(\(\d{2,4}\)[^(]+)+", s.replace(" ", "")) else None
    s = s.lstrip(GS)
    out, i = [], 0
    while i < len(s):
        spec = _ai_spec(s, i)
        if not spec:
            # An AI we don't know: keep what was read when it began with a valid (01) GTIN, so the
            # product is still found; otherwise it isn't GS1 we can trust.
            return out if out and out[0][0] == "01" and valid_gtin(out[0][1]) else None
        ai, fixed, mx = spec
        i += len(ai)
        if fixed is not None:
            val = s[i:i + fixed]
            if len(val) != fixed:
                return None
            i += fixed
            if i < len(s) and s[i] == GS:      # a GS after a fixed field is allowed
                i += 1
        else:
            end = s.find(GS, i)
            end = len(s) if end < 0 else end
            val = s[i:end]
            if not val or len(val) > mx:
                return None
            i = end + 1
        if any(a == ai for a, _ in out):
            return None
        out.append((ai, val))
    return out or None


def _gs1_date(v: str) -> Optional[date]:
    """YYMMDD → date; DD 00 means the last day of the month (GS1 rule); century per GS1 sliding window."""
    if not (v and len(v) == 6 and is_digits(v)):
        return None
    yy, mm, dd = int(v[:2]), int(v[2:4]), int(v[4:])
    if not 1 <= mm <= 12:
        return None
    this = date.today().year
    year = (this // 100) * 100 + yy
    if year - this > 50:
        year -= 100
    elif this - year > 49:
        year += 100
    if dd == 0:
        import calendar
        dd = calendar.monthrange(year, mm)[1]
    try:
        return date(year, mm, dd)
    except ValueError:
        return None


def to_hri(ais: list[tuple[str, str]]) -> str:
    return "".join(f"({a}){v}" for a, v in ais)


# ── scan reading ─────────────────────────────────────────────────────────────────────────────────
@dataclass
class Scan:
    raw: str
    text: str                                  # without AIM prefix / control characters
    symbology: Optional[str] = None            # AIM id, e.g. "]E0", when the scanner sent one
    ais: list = field(default_factory=list)    # GS1 [(AI, value)] when it was a GS1 string
    gtin: Optional[str] = None                 # GTIN-14 from (01), or the code itself when it's a GTIN
    lot: Optional[str] = None
    expiry: Optional[date] = None
    serial: Optional[str] = None
    keys: list = field(default_factory=list)   # match keys to look up, best first
    clean: str = ""                            # the code as sent, minus AIM prefix / invisible characters
    fallback_keys: list = field(default_factory=list)   # used only when no product has one of `keys`

    @property
    def is_gs1(self) -> bool:
        return bool(self.ais)

    def as_dict(self) -> dict:
        return {"text": self.text, "symbology": self.symbology, "gs1": to_hri(self.ais) if self.ais else None,
                "gtin": self.gtin, "lot": self.lot, "expiry": self.expiry.isoformat() if self.expiry else None,
                "serial": self.serial, "keys": self.keys}

    def all_texts(self) -> list:
        """Every literal form worth comparing with SKU / code / serial: as parsed and as sent."""
        return list(dict.fromkeys(t for t in (self.text, self.clean) if t))


def _clean(raw: str) -> tuple[str, Optional[str], bool]:
    s = str(raw or "")[:MAX_SCAN]
    for sp in _GS_SPELLINGS:
        s = s.replace(sp, GS)
    s = s.strip(" \t\r\n")
    sym, gs1 = None, False
    m = _AIM.match(s)
    if m:
        sym = s[:3]
        gs1 = (m.group("sym"), m.group("mod")) in _GS1_AIM
        s = s[3:]
    # Keep GS (it separates GS1 fields); drop any other control character a scanner may add.
    s = "".join(ch for ch in s if ch == GS or (ord(ch) >= 32 and ch not in _INVISIBLE)).strip()
    return s, sym, gs1


def gtin_keys(v: str) -> list[str]:
    """Every GTIN-14 a plain digit string could be (an 8-digit code can be EAN-8 or UPC-E)."""
    out = []
    if not is_digits(v):
        return out
    if len(v) in (12, 13, 14) and valid_gtin(v):
        out.append(v.zfill(14))
    if len(v) == 8:
        if valid_gtin(v):
            out.append(v.zfill(14))
        upca = upce_to_upca(v)
        if upca:
            out.append(upca.zfill(14))
    return list(dict.fromkeys(out))


def parse(raw: str) -> Scan:
    text, sym, gs1_flag = _clean(raw)
    scan = Scan(raw=str(raw or ""), text=text.replace(GS, ""), symbology=sym, clean=text.replace(GS, ""))
    ais = None
    if gs1_flag or GS in text or text.startswith("(") or (text.startswith("01") and len(text) >= 16):
        ais = parse_gs1(text)
        if ais and not gs1_flag and GS not in text and not text.startswith("("):
            # Unbracketed, unflagged: only trust it when it begins with a valid (01) GTIN.
            if ais[0][0] != "01" or not valid_gtin(ais[0][1]):
                ais = None
    if ais:
        scan.ais = ais
        vals = dict(ais)
        scan.text = to_hri(ais)
        if "01" in vals and valid_gtin(vals["01"]):
            scan.gtin = vals["01"]
        scan.lot = vals.get("10")
        scan.serial = vals.get("21")
        scan.expiry = _gs1_date(vals.get("17", ""))
        keys = [scan.gtin] if scan.gtin else []
        keys.append(scan.text)
    else:
        keys = gtin_keys(text)
        if len(text) == 8 and len(keys) == 2:
            # Both an EAN-8 and a UPC-E. The scanner's AIM id settles it (]E4 = EAN-8, ]E0 = UPC-E);
            # without one, EAN-8 wins and the UPC-E reading is used only when no product has the EAN-8.
            ean8, upce = keys
            if sym == "]E0":
                keys = [upce]
            elif sym == "]E4":
                keys = [ean8]
            else:
                keys, scan.fallback_keys = [ean8], [upce]
        if keys and len(text) != 8:
            scan.gtin = keys[0]
        keys.append(scan.text)
    # The code as sent is always a key too: a Code 128 / serial that merely looks like GS1 or a GTIN
    # must still find what it was stored as.
    keys.append(scan.clean)
    scan.keys = list(dict.fromkeys(k for k in keys if k))
    return scan


def match_key(value: Optional[str], kind: Optional[str] = None) -> Optional[str]:
    """The key a stored barcode is unique on and found by. GTINs (any of their forms, UPC-E expanded,
    or the (01) of a GS1 string) → GTIN-14; anything else → the code itself."""
    v = str(value or "").strip()
    if not v:
        return None
    if kind == "upce":
        upca = upce_to_upca(v)
        return upca.zfill(14) if upca else v
    if is_digits(v):
        if len(v) in (12, 13, 14) and valid_gtin(v):
            return v.zfill(14)
        if len(v) == 8:
            if kind != "upce" and valid_gtin(v):
                return v.zfill(14)
            upca = upce_to_upca(v)
            if upca:
                return upca.zfill(14)
        return v
    if v.startswith("(") or GS in v:
        ais = parse_gs1(v)
        if ais:
            g = dict(ais).get("01")
            return g.zfill(14) if g and valid_gtin(g) else to_hri(ais)
    return v
