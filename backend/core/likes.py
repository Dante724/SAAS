"""Search with LIKE without the user's text acting as a wildcard.

`%` and `_` typed into a search box are literal characters: a product called "50% off" is
found by searching "50%", and searching "%" no longer lists every row.
"""

_ESC = "\\"


def like_escape(text: str) -> str:
    return text.replace(_ESC, _ESC * 2).replace("%", _ESC + "%").replace("_", _ESC + "_")


def contains(col, text: str):
    """Case-insensitive "col contains text"."""
    return col.ilike(f"%{like_escape(text)}%", escape=_ESC)
