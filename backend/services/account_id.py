"""Shop account IDs: short, unique, typo-proof, e.g. ``TK-7K4M-92Q``.

- 31 symbols with no 0/O/1/I/L, drawn at random (never counting up, so an ID says
  nothing about how many shops exist).
- The last character is a check: a weighted sum mod 31 (a prime), which catches every
  single-character typo and every swap of two neighbouring characters.
- Uniqueness is the database's job (UNIQUE column); callers retry on a clash.
- Never used to sign in, so it is safe to print and read out on the phone.
"""
import re
import secrets

ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"   # 31 symbols
_N = len(ALPHABET)
_BODY = 6
_SHAPE = re.compile(r"^TK[%s]{%d}$" % (ALPHABET, _BODY + 1))


def check_char(body: str) -> str:
    return ALPHABET[sum((i + 1) * ALPHABET.index(c) for i, c in enumerate(body)) % _N]


def new_account_id() -> str:
    body = "".join(secrets.choice(ALPHABET) for _ in range(_BODY))
    return f"TK-{body[:4]}-{body[4:]}{check_char(body)}"


def normalize(raw: str | None) -> str | None:
    """Accept lowercase, spaces and missing dashes; return the canonical ``TK-XXXX-XXC``
    or None when it isn't a well-formed ID with a correct check character."""
    if not raw:
        return None
    s = re.sub(r"[\s\-]", "", str(raw)).upper()
    if len(s) == _BODY + 1:   # typed without the TK prefix (even if the body starts with TK)
        s = "TK" + s
    if not _SHAPE.match(s):
        return None
    body, chk = s[2:2 + _BODY], s[2 + _BODY]
    if check_char(body) != chk:
        return None
    return f"TK-{body[:4]}-{body[4:]}{chk}"


def looks_like_account_id(raw: str | None) -> bool:
    return normalize(raw) is not None
