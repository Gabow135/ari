from ari.infrastructure.vault_web.file_link_store import FileLinkStore


def test_claim_returns_path_once_then_none():
    clock = lambda: 100.0
    s = FileLinkStore(ttl_seconds=60, clock=clock)
    tok = s.create("/tmp/a.pdf")
    assert s.claim(tok) == "/tmp/a.pdf"
    assert s.claim(tok) is None  # single-use


def test_expired_token_is_none():
    now = {"t": 100.0}
    s = FileLinkStore(ttl_seconds=60, clock=lambda: now["t"])
    tok = s.create("/tmp/a.pdf")
    now["t"] = 161.0
    assert s.claim(tok) is None


def test_unknown_token_is_none():
    s = FileLinkStore(ttl_seconds=60, clock=lambda: 0.0)
    assert s.claim("nope") is None


def test_active_count_reflects_create_and_claim():
    s = FileLinkStore(ttl_seconds=60, clock=lambda: 0.0)
    t1 = s.create("/tmp/a"); s.create("/tmp/b")
    assert s.active_count() == 2
    s.claim(t1)
    assert s.active_count() == 1
