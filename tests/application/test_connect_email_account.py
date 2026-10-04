import base64
import json

import pytest
from cryptography.fernet import Fernet
from nacl.public import PublicKey, SealedBox

from ari.application.email.connect_email_account import (
    ConnectEmailAccount, is_email_blob,
)
from ari.infrastructure.crypto.fernet_cipher import FernetCipher
from ari.infrastructure.email.sealed_box import AriSealedBox
from ari.infrastructure.email.sqlite_email_accounts import SqliteEmailAccounts
from ari.infrastructure.persistence.db import connect


class FakeVault:
    def __init__(self): self._d = {}
    def get(self, n): return self._d.get(n)
    def set(self, n, v): self._d[n] = v


def _blob(box, payload):
    pub = base64.b64decode(box.public_key_b64())
    sealed = SealedBox(PublicKey(pub)).encrypt(json.dumps(payload).encode())
    return "ari-mail:v1:" + base64.b64encode(sealed).decode()


def _payload(label="trabajo"):
    return {"label": label, "imap_host": "imap.x.com", "imap_port": 993,
            "imap_secure": True, "smtp_host": "smtp.x.com", "smtp_port": 465,
            "smtp_secure": True, "email_user": "juan@x.com", "password": "pw"}


@pytest.fixture
async def ctx():
    conn = await connect(":memory:", embedding_dim=4)
    box = AriSealedBox(FakeVault())
    box.public_key_b64()  # mint
    accounts = SqliteEmailAccounts(conn, FernetCipher(Fernet.generate_key().decode()))
    yield box, accounts, conn
    await conn.close()


def test_is_email_blob():
    assert is_email_blob("ari-mail:v1:abc")
    assert not is_email_blob("hola")


async def test_valid_blob_stored_under_actor(ctx):
    box, accounts, _ = ctx
    use = ConnectEmailAccount(box, accounts)
    label = await use("7", _blob(box, _payload()))
    assert label == "trabajo"
    assert [a.email_user for a in await accounts.list_for_user("7")] == ["juan@x.com"]


async def test_bad_label_rejected_stores_nothing(ctx):
    box, accounts, _ = ctx
    use = ConnectEmailAccount(box, accounts)
    with pytest.raises(ValueError):
        await use("7", _blob(box, _payload(label="../evil")))
    assert await accounts.list_for_user("7") == []


async def test_garbage_blob_rejected(ctx):
    box, accounts, _ = ctx
    use = ConnectEmailAccount(box, accounts)
    with pytest.raises(ValueError):
        await use("7", "ari-mail:v1:not-base64-!!")
