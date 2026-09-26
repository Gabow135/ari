"""Owns the vault web maintainer's lifecycle: detects the LAN IP, prepares the cert,
starts the HTTPS server on demand, hands out links, and shuts the server down when no
tokens remain."""
import logging
import socket
import threading
import time

from ari.infrastructure.vault_web.cert import ensure_cert
from ari.infrastructure.vault_web.link_store import VaultLinkStore
from ari.infrastructure.vault_web.names import configurable_secret_names
from ari.infrastructure.vault_web.server import VaultWebServer

log = logging.getLogger("ari.vault_web")


def detect_lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        log.warning("could not detect LAN IP; falling back to 127.0.0.1")
        return "127.0.0.1"
    finally:
        s.close()


class VaultWebMaintainer:
    def __init__(self, vault, servers_json: str, cert_dir: str, port: int, bind: str,
                 ttl_minutes: int, clock=time.monotonic):
        self._vault = vault
        self._servers_json = servers_json
        self._cert_dir, self._port, self._bind = cert_dir, port, bind
        self._store = VaultLinkStore(ttl_minutes * 60, clock)
        self._server: VaultWebServer | None = None
        self._lock = threading.Lock()

    def new_link(self) -> str:
        with self._lock:
            lan_ip = detect_lan_ip()
            if self._server is None:
                cert, key = ensure_cert(self._cert_dir, lan_ip)
                names = configurable_secret_names(self._servers_json)
                self._server = VaultWebServer(self._bind, self._port, cert, key,
                                              self._vault, self._store, names)
                self._server.start()
            token = self._store.create()
            return f"https://{lan_ip}:{self._server.port}/v/{token}"

    def sweep_and_maybe_stop(self) -> None:
        with self._lock:
            if self._server is not None and self._store.active_count() == 0:
                self._server.stop()
                self._server = None

    def stop(self) -> None:
        with self._lock:
            if self._server is not None:
                self._server.stop()
                self._server = None
