"""w4i01 (INV-02): order_lines.stock_draws is added in place and dropped in place.

A plain ADD COLUMN on SQLite (no table rebuild), so every index on order_lines survives and the
existing lines keep their data; downgrade drops the column and upgrade adds it again."""
import sqlite3

from backend.tests.test_dbi_migrations import _insert, _ok, _two_shops


def _cols(con) -> set:
    return {r[1] for r in con.execute("PRAGMA table_info('order_lines')")}


def _indexes(con) -> set:
    return {(r[0], r[1]) for r in con.execute(
        "SELECT name, sql FROM sqlite_master WHERE type = 'index' AND tbl_name = 'order_lines'")}


def test_w4i01_up_down_up_keeps_lines_and_indexes(tmp_path):
    path = tmp_path / "w4i01.db"
    url = f"sqlite+aiosqlite:///{path}"
    _ok(url, "upgrade", "dbi03")
    con = sqlite3.connect(path)
    _two_shops(con)
    _insert(con, "order_lines", {"id": "ln-1", "org_id": "orgA", "order_id": "ord-orgA", "line_type": "part",
                                 "description": "Screen", "qty": 2})
    con.commit()
    before = _indexes(con)
    assert "stock_draws" not in _cols(con)
    con.close()

    _ok(url, "upgrade", "w4i01")
    con = sqlite3.connect(path)
    assert "stock_draws" in _cols(con)
    assert _indexes(con) == before
    assert con.execute("SELECT description, stock_draws FROM order_lines WHERE id = 'ln-1'").fetchone() == ("Screen", None)
    con.execute("""UPDATE order_lines SET stock_draws = '{"layers": []}' WHERE id = 'ln-1'""")
    con.commit()
    con.close()

    _ok(url, "downgrade", "dbi03")
    con = sqlite3.connect(path)
    assert "stock_draws" not in _cols(con)
    assert _indexes(con) == before
    assert con.execute("SELECT description FROM order_lines WHERE id = 'ln-1'").fetchone() == ("Screen",)
    con.close()

    _ok(url, "upgrade", "w4i01")
    con = sqlite3.connect(path)
    assert "stock_draws" in _cols(con)
    con.close()
