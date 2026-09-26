from ari.infrastructure.vault_web.link_store import VaultLinkStore


def test_token_valid_before_ttl_and_invalid_after():
    now = [1000.0]
    store = VaultLinkStore(ttl_seconds=600, clock=lambda: now[0])
    tok = store.create()
    assert store.valid(tok)
    now[0] += 599
    assert store.valid(tok)          # still inside the window
    now[0] += 2                      # 1601 > 1000 + 600
    assert not store.valid(tok)
    assert store.active_count() == 0


def test_unknown_token_is_invalid():
    store = VaultLinkStore(ttl_seconds=600, clock=lambda: 0.0)
    assert not store.valid("nope")


def test_tokens_are_unique_and_long():
    store = VaultLinkStore(ttl_seconds=600, clock=lambda: 0.0)
    a, b = store.create(), store.create()
    assert a != b and len(a) >= 40 and store.active_count() == 2
