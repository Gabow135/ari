import pytest
from cryptography.fernet import Fernet

from ari.domain.email.entities import EmailAccount
from ari.infrastructure.crypto.fernet_cipher import FernetCipher
from ari.infrastructure.email.sqlite_email_accounts import SqliteEmailAccounts
from ari.infrastructure.persistence.db import connect


def _acct(user_id="7", label="trabajo", password="s3cret"):
    return EmailAccount(user_id, label, "imap.x.com", 993, True,
                        "smtp.x.com", 465, True, "juan@x.com", password)


@pytest.fixture
async def cipher():
    return FernetCipher(Fernet.generate_key().decode())


@pytest.fixture
async def store(cipher):
    conn = await connect(":memory:", embedding_dim=4)
    yield SqliteEmailAccounts(conn, cipher)
    await conn.close()


async def test_add_and_list_round_trip(store):
    await store.add(_acct())
    got = await store.list_for_user("7")
    assert [(a.label, a.email_user, a.password) for a in got] == [("trabajo", "juan@x.com", "s3cret")]


async def test_password_is_ciphertext_at_rest(store, cipher):
    await store.add(_acct(password="plain-pw"))
    rows = await store._conn.execute_fetchall(
        "SELECT pass_enc FROM user_email_accounts")
    assert rows[0]["pass_enc"] != "plain-pw"
    assert cipher.decrypt(rows[0]["pass_enc"]) == "plain-pw"


async def test_isolation_between_users(store):
    await store.add(_acct(user_id="7", label="a"))
    await store.add(_acct(user_id="9", label="b"))
    assert [a.label for a in await store.list_for_user("7")] == ["a"]


async def test_summaries_mask_and_need_no_cipher(store):
    await store.add(_acct())
    conn = store._conn
    cipherless = SqliteEmailAccounts(conn, None)
    sums = await cipherless.summaries_for("7")
    assert [(s.label, s.address) for s in sums] == [("trabajo", "ju***@x.com")]


async def test_remove_and_delete_for_user(store):
    await store.add(_acct(label="a"))
    await store.add(_acct(label="b"))
    assert await store.remove("7", "a") is True
    assert await store.remove("7", "nope") is False
    assert await store.delete_for_user("7") == 1


async def test_list_without_cipher_raises(store):
    await store.add(_acct())
    with pytest.raises(RuntimeError):
        await SqliteEmailAccounts(store._conn, None).list_for_user("7")
