import asyncio
import types

import pytest


class _FakeConn:
    def __init__(self, exc):
        self._exc = exc
        self.executed = []

    async def execute(self, stmt):
        self.executed.append(str(stmt))
        raise self._exc


class _FakeBegin:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeEngine:
    def __init__(self, conn, dialect_name):
        self._conn = conn
        self.dialect = types.SimpleNamespace(name=dialect_name)

    def begin(self):
        return _FakeBegin(self._conn)


def test_add_team_style_tag_unknown_dialect_raises(monkeypatch):
    from tools import migrate_market_flow as mmf

    conn = _FakeConn(RuntimeError("boom"))
    monkeypatch.setattr(mmf, "engine", types.SimpleNamespace(dialect=types.SimpleNamespace(name="mysql")))

    async def _run():
        await mmf._add_team_style_tag(conn)

    with pytest.raises(RuntimeError) as excinfo:
        asyncio.run(_run())

    msg = str(excinfo.value)
    assert "Unknown SQL dialect 'mysql'" in msg
    assert "ALTER TABLE teams ADD COLUMN style_tag" in msg
    assert "boom" in msg


def test_main_returns_nonzero_on_unknown_dialect(monkeypatch, capsys):
    from tools import migrate_market_flow as mmf

    conn = _FakeConn(Exception("boom"))
    monkeypatch.setattr(mmf, "engine", _FakeEngine(conn, "mysql"))

    rc = asyncio.run(mmf.main())
    assert rc == 1

    captured = capsys.readouterr()
    assert "migrate_market_flow failed:" in captured.err
