import pytest
from zoneinfo import ZoneInfo
from datetime import UTC, datetime

from ari.application.ari_tools import Actor, AriTools
from ari.domain.email.entities import EmailAccount
from ari.infrastructure.crypto.fernet_cipher import FernetCipher
from ari.infrastructure.email.sqlite_email_accounts import SqliteEmailAccounts
from ari.infrastructure.persistence.db import connect
from ari.infrastructure.persistence.sqlite_email_enroll_requests import (
    SqliteEmailEnrollRequests,
)
from ari.infrastructure.persistence.sqlite_turn_log import SqliteTurnLog
from cryptography.fernet import Fernet


def _actor(context="chat", is_owner=False):
    return Actor(user_id="7", chat_id="7", name="Juan", is_owner=is_owner,
                 context=context, turn_id="t1")


@pytest.fixture
async def tools():
    conn = await connect(":memory:", embedding_dim=4)
    accounts = SqliteEmailAccounts(conn, FernetCipher(Fernet.generate_key().decode()))
    enroll = SqliteEmailEnrollRequests(conn)
    t = AriTools(_actor(), schedule=None, memory=None, turn_log=SqliteTurnLog(conn),
                 tz=ZoneInfo("UTC"), max_items=50, clock=lambda: datetime.now(UTC),
                 email_accounts=accounts, email_enroll=enroll)
    yield t, accounts, enroll
    await conn.close()


async def test_conectar_correo_enqueues(tools):
    t, _, enroll = tools
    out = await t.conectar_correo()
    assert "correo" in out.lower()
    assert len(await enroll.claim_pending()) == 1


async def test_mis_correos_masks_address(tools):
    t, accounts, _ = tools
    await accounts.add(EmailAccount("7", "trabajo", "h", 993, True, "h", 465, True,
                                    "juan@x.com", "pw"))
    out = await t.mis_correos()
    assert "trabajo" in out and "ju***@x.com" in out
    assert "pw" not in out


async def test_olvidar_correo_removes(tools):
    t, accounts, _ = tools
    await accounts.add(EmailAccount("7", "trabajo", "h", 993, True, "h", 465, True,
                                    "juan@x.com", "pw"))
    out = await t.olvidar_correo("trabajo")
    assert "trabajo" in out
    assert await accounts.summaries_for("7") == []
