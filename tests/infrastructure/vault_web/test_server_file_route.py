import http.client
import ssl

import pytest

from ari.infrastructure.vault_web.cert import ensure_cert
from ari.infrastructure.vault_web.file_link_store import FileLinkStore
from ari.infrastructure.vault_web.server import VaultWebServer


class _Vault:
    def names(self): return []


def _get(port, path):
    ctx = ssl._create_unverified_context()
    conn = http.client.HTTPSConnection("127.0.0.1", port, context=ctx)
    conn.request("GET", path)
    r = conn.getresponse()
    body = r.read()
    conn.close()
    return r.status, body


def test_file_route_streams_once_then_403(tmp_path):
    f = tmp_path / "factura.txt"; f.write_text("TOTAL 16,61")
    cert, key = ensure_cert(str(tmp_path), "127.0.0.1")
    fs = FileLinkStore(ttl_seconds=60, clock=lambda: 0.0)
    srv = VaultWebServer("127.0.0.1", 0, cert, key, _Vault(), fs, [],
                         file_store=fs, denied_roots=[])
    srv.start()
    try:
        tok = fs.create(str(f))
        status, body = _get(srv.port, f"/f/{tok}")
        assert status == 200 and body == b"TOTAL 16,61"
        status2, _ = _get(srv.port, f"/f/{tok}")
        assert status2 == 403  # single-use consumed
    finally:
        srv.stop()


def test_file_route_denied_path_refused(tmp_path):
    secret = tmp_path / ".env"; secret.write_text("S=1")
    cert, key = ensure_cert(str(tmp_path), "127.0.0.1")
    fs = FileLinkStore(ttl_seconds=60, clock=lambda: 0.0)
    srv = VaultWebServer("127.0.0.1", 0, cert, key, _Vault(), fs, [],
                         file_store=fs, denied_roots=[])
    srv.start()
    try:
        tok = fs.create(str(secret))  # a .env sneaked into the store
        status, _ = _get(srv.port, f"/f/{tok}")
        assert status == 404  # serve-time denylist recheck (.env by basename)
    finally:
        srv.stop()
