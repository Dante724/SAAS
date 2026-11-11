"""Shared matching for search boxes: phone numbers however they're typed, and document numbers
with or without their prefix. Portable SQL (SQLite in tests, Postgres in production).

    stripped(col, PHONE_STRIP)       the column with phone formatting removed
    phone_needles("+1 415-555-0134") digit strings to look for inside a stripped stored phone
    phone_match(Client.phone, s)     a ready condition (None when `s` isn't phone-shaped)
    number_match(Order.order_number, s)  "WO-00012" ~ "wo00012", "WO12", "#12", "12"
    words_match(Client.name, s)      every word of `s` somewhere in the column, any order/case
    words_in_any([brand, model], s)  every word of `s` in at least one of the columns
    code_match(Part.sku, s)          a code with separators and case ignored ("SCR-IP12" ~ "scr ip12")
"""
import re

from sqlalchemy import and_, func, or_

from backend.services.call_contacts import code_for_number, digits

NUM_STRIP = (" ", "-", "#", "/", ".", "_")
PHONE_STRIP = (" ", "-", "(", ")", ".", "/", "+")
_LETTERS = "abcdefghijklmnopqrstuvwxyz"


def stripped(col, chars):
    """The column with formatting characters removed."""
    for ch in chars:
        col = func.replace(col, ch, "")
    return col


def phone_needles(s: str) -> set:
    """The digit strings a typed phone number can appear as inside a stored one with its
    formatting stripped: as typed, without the country code ("+1 415…", "0044 20…",
    "1-415-…") and without the national trunk 0 ("020 7946…"). Empty when it isn't a phone."""
    if re.search(r"[A-Za-z]", s or ""):
        return set()
    d = digits(s)
    if len(d) < 4:
        return set()
    out = {d}
    code = code_for_number(s)
    if code and len(d) - len(code) >= 6:
        out.add(d[len(code):])
    if len(d) == 11 and d.startswith("1"):           # NANP typed with the 1 but no +
        out.add(d[1:])
    for n in list(out):
        if n.startswith("0") and len(n) >= 7:       # national trunk prefix
            out.add(n.lstrip("0"))
    return out


def phone_match(col, s: str):
    needles = phone_needles(s)
    if not needles:
        return None
    flat = stripped(col, PHONE_STRIP)
    return and_(col.is_not(None), or_(*[flat.contains(n) for n in needles]))


def number_match(col, s: str):
    """A document number compared with separators dropped and case folded; a number-shaped
    query also matches on the number itself with its zero padding ignored."""
    flat = stripped(func.lower(col), NUM_STRIP)
    qn = (s or "").lower()
    for ch in NUM_STRIP:
        qn = qn.replace(ch, "")
    if not qn:
        return None
    conds = [flat.contains(qn, autoescape=True)]
    m = re.fullmatch(r"([a-z]*)0*(\d+)", qn)
    if m:
        prefix, n = m.group(1), m.group(2)
        same_n = func.ltrim(flat, _LETTERS + "0") == n
        conds.append(and_(flat.startswith(prefix, autoescape=True), same_n) if prefix else same_n)
    return or_(*conds)


def words_match(col, s: str):
    return and_(*[col.icontains(w, autoescape=True) for w in (s or "").split()])


def words_in_any(cols, s: str, extra=None):
    """Every word of `s` appears in at least one of `cols` (any order, any case). `extra(word)`
    can add one more condition per word, e.g. an org-scoped subquery on the owner's name."""
    per_word = []
    for w in (s or "").split():
        alts = [c.icontains(w, autoescape=True) for c in cols]
        if extra is not None:
            alts.append(extra(w))
        per_word.append(or_(*alts))
    return and_(*per_word) if per_word else None


def code_match(col, s: str):
    """A code (SKU, serial, VIN…) compared with separators dropped and case folded."""
    qn = (s or "").lower()
    for ch in NUM_STRIP:
        qn = qn.replace(ch, "")
    if not qn:
        return None
    return stripped(func.lower(col), NUM_STRIP).contains(qn, autoescape=True)


def contact_match(name_cols, phone_col, email_col, s: str):
    """A person/company search box: name words (any order, across `name_cols`), the phone
    however it's typed, or the email."""
    conds = [words_in_any(name_cols, s), email_col.icontains(s, autoescape=True)]
    phone = phone_match(phone_col, s)
    if phone is not None:
        conds.append(phone)
    return or_(*[c for c in conds if c is not None])
