import http.client
import ssl
import urllib.parse

import pytest

from ari.infrastructure.vault_web.cert import ensure_cert
from ari.infrastructure.vault_web.link_store import VaultLinkStore
from ari.infrastructure.vault_web.server import VaultWebServer
from tests.fakes import FakeVault

NAMES = ["GOOGLE_OAUTH_CLIENT_SECRET", "ARI_MYSQL_PASS"]


@pytest.fixture
def running(tmp_path):
    cert, key = ensure_cert(str(tmp_path), "127.0.0.1")
    store = VaultLinkStore(ttl_seconds=600, clock=lambda: 0.0)
    vault = FakeVault({"GOOGLE_OAUTH_CLIENT_SECRET": "already-set"})
    srv = VaultWebServer("127.0.0.1", 0, cert, key, vault, store, NAMES)
    srv.start()
    yield srv, store, vault
    srv.stop()


def _conn(srv):
    ctx = ssl._create_unverified_context()
    return http.client.HTTPSConnection("127.0.0.1", srv.port, context=ctx, timeout=5)


def _get(srv, path):
    c = _conn(srv); c.request("GET", path); r = c.getresponse()
    body = r.read().decode(); headers = r.headers; c.close(); return r.status, body, headers


def _get_simple(srv, path):
    status, body, _ = _get(srv, path)
    return status, body


def _post(srv, path, fields):
    c = _conn(srv)
    body = urllib.parse.urlencode(fields)
    c.request("POST", path, body, {"Content-Type": "application/x-www-form-urlencoded"})
    r = c.getresponse(); out = r.read().decode(); c.close(); return r.status, out


def test_valid_get_renders_form_without_values(running):
    srv, store, vault = running
    tok = store.create()
    status, body, _ = _get(srv, f"/v/{tok}")
    assert status == 200
    assert "GOOGLE_OAUTH_CLIENT_SECRET" in body and "ARI_MYSQL_PASS" in body
    assert "already-set" not in body           # a stored value is never echoed
    assert "cargado" in body and "falta" in body


def test_invalid_and_expired_token_are_403(running):
    srv, store, vault = running
    assert _get(srv, "/v/bogus")[0] == 403  # status is index 0 of (status, body, headers)
    assert _post(srv, "/v/bogus", {"ARI_MYSQL_PASS": "x"})[0] == 403


def test_post_stores_allowlisted_value(running):
    srv, store, vault = running
    tok = store.create()
    status, _ = _post(srv, f"/v/{tok}", {"ARI_MYSQL_PASS": "s3cr+t/=&x"})
    assert status == 200
    assert vault.get("ARI_MYSQL_PASS") == "s3cr+t/=&x"    # URL-special chars round-trip


def test_post_empty_value_is_skipped(running):
    srv, store, vault = running
    tok = store.create()
    _post(srv, f"/v/{tok}", {"ARI_MYSQL_PASS": ""})
    assert vault.get("ARI_MYSQL_PASS") is None


def test_post_name_outside_allowlist_is_400(running):
    srv, store, vault = running
    tok = store.create()
    assert _post(srv, f"/v/{tok}", {"EVIL_KEY": "x"})[0] == 400
    assert vault.get("EVIL_KEY") is None


def test_logs_never_contain_token_or_value(running, caplog):
    srv, store, vault = running
    tok = store.create()
    with caplog.at_level("INFO", logger="ari.vault_web"):
        _post(srv, f"/v/{tok}", {"ARI_MYSQL_PASS": "topsecret"})
    text = caplog.text
    assert tok not in text and "topsecret" not in text


def test_security_headers_present_on_every_response(running):
    srv, store, vault = running
    tok = store.create()
    _, _, headers = _get(srv, f"/v/{tok}")
    assert headers.get("Cache-Control") == "no-store"
    assert headers.get("X-Content-Type-Options") == "nosniff"


def test_trailing_slash_token_returns_200(running):
    srv, store, vault = running
    tok = store.create()
    status, _, _ = _get(srv, f"/v/{tok}/")
    assert status == 200


def test_invalid_token_trailing_slash_returns_403(running):
    srv, store, vault = running
    assert _get(srv, "/v/bogus/")[0] == 403


def test_non_v_path_returns_404(running):
    srv, store, vault = running
    assert _get(srv, "/other/path")[0] == 404
