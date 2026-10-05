# tests/application/test_ari_tools_email_reader.py
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from ari.application.ari_tools import Actor, AriTools
from ari.domain.email.entities import EmailAttachment, EmailSummary, FetchedEmail, MailboxSpec
from ari.domain.tools.ari_permissions import CHAT
from ari.infrastructure.memory.sqlite_memory_adapter import SqliteMemoryAdapter
from ari.infrastructure.persistence.db import connect
from ari.infrastructure.persistence.sqlite_turn_log import SqliteTurnLog
from ari.infrastructure.schedule.sqlite_schedule_store import SqliteScheduleStore
from ari.infrastructure.workspace.user_workspace import Workspaces

TZ = ZoneInfo("America/Guayaquil")
NOW = datetime(2026, 10, 5, 15, 0, tzinfo=UTC)
SPEC = MailboxSpec("email_corp", "imap.x.com", 993, True, "u@x.com", "pw")


class FakeReader:
    def __init__(self, summaries=None, message=None, boom=False):
        self._summaries, self._message, self._boom = summaries or [], message, boom

    def search(self, spec, criteria, limit):
        if self._boom:
            raise OSError("imap down")
        return self._summaries[:limit]

    def fetch(self, spec, uid):
        if self._boom:
            raise OSError("imap down")
        if self._message is None:
            raise LookupError(uid)
        return self._message


@pytest.fixture
async def env(tmp_path):
    conn = await connect(":memory:", embedding_dim=4)
    yield conn, Workspaces(str(tmp_path)), SqliteTurnLog(conn)
    await conn.close()


def _tools(env, *, reader, mailboxes=None):
    conn, workspaces, log = env
    actor = Actor("42", "42", "Gabriel", True, CHAT, "t1")
    return AriTools(actor, schedule=SqliteScheduleStore(conn),
                    memory=SqliteMemoryAdapter(conn, embedding_dim=4), turn_log=log,
                    tz=TZ, max_items=20, clock=lambda: NOW, workspaces=workspaces,
                    email_reader=reader,
                    mailboxes=mailboxes if mailboxes is not None else {"email_corp": SPEC})


async def test_buscar_lists_results(env):
    reader = FakeReader(summaries=[
        EmailSummary("3", "pay@mrjoy.com", "Factura", "5 Oct", ""),
        EmailSummary("2", "no@netlife.com", "Pago", "1 Oct", "")])
    out = await _tools(env, reader=reader).buscar_correos("email_corp")
    assert "#3" in out and "Factura" in out and "#2" in out


async def test_buscar_unknown_account(env):
    out = await _tools(env, reader=FakeReader()).buscar_correos("gmail")
    assert "no tenés una casilla" in out.lower()


async def test_buscar_no_mailboxes(env):
    out = await _tools(env, reader=FakeReader(), mailboxes={}).buscar_correos("x")
    assert "no tenés casillas" in out.lower()


async def test_buscar_connection_error_is_friendly(env):
    out = await _tools(env, reader=FakeReader(boom=True)).buscar_correos("email_corp")
    assert "no pude conectarme" in out.lower()
    assert "pw" not in out  # never leaks the password


def _msg(body="Total: 1.234 USD", attachments=()):
    return FetchedEmail("7", "pay@mrjoy.com", "me@corp.com", "Factura",
                        "5 Oct", body, tuple(attachments))


async def test_leer_correo_formats_and_spills_body(env):
    _conn, workspaces, _ = env
    reader = FakeReader(message=_msg())
    out = await _tools(env, reader=reader).leer_correo("email_corp", "7")
    assert "Asunto: Factura" in out
    assert "Total: 1.234 USD" in out
    assert "correos/email_corp-7.txt" in out
    ws = workspaces.for_user("42")
    assert "1.234" in ws.read_text("correos/email_corp-7.txt")


async def test_leer_correo_saves_attachments(env):
    _conn, workspaces, _ = env
    att = EmailAttachment("factura.pdf", "application/pdf", b"%PDF bytes")
    out = await _tools(env, reader=FakeReader(message=_msg(attachments=[att]))
                       ).leer_correo("email_corp", "7")
    assert "correos/adjuntos/email_corp-7/01-factura.pdf" in out
    ws = workspaces.for_user("42")
    assert ws.read_bytes("correos/adjuntos/email_corp-7/01-factura.pdf") == b"%PDF bytes"


async def test_leer_correo_sanitizes_attachment_name(env):
    _conn, workspaces, _ = env
    att = EmailAttachment("../../etc/passwd", "text/plain", b"x")
    out = await _tools(env, reader=FakeReader(message=_msg(attachments=[att]))
                       ).leer_correo("email_corp", "7")
    # saved safely inside the per-email folder, never escaping
    assert "correos/adjuntos/email_corp-7/" in out
    assert ".." not in out.split("Adjuntos")[-1]
    ws = workspaces.for_user("42")
    assert ws.read_bytes("correos/adjuntos/email_corp-7/01-passwd") == b"x"


async def test_leer_correo_truncates_body_over_text_cap(env):
    _conn, workspaces, _ = env
    big = "A" * 1_200_000  # over the 1 MB text cap
    await _tools(env, reader=FakeReader(message=_msg(body=big))
                 ).leer_correo("email_corp", "7")
    ws = workspaces.for_user("42")
    saved = ws.read_text("correos/email_corp-7.txt")
    assert "(truncado)" in saved and len(saved.encode("utf-8")) <= 1_048_576 + 50


async def test_leer_correo_missing_uid(env):
    out = await _tools(env, reader=FakeReader(message=None)).leer_correo("email_corp", "999")
    assert "no encontré el correo" in out.lower()
