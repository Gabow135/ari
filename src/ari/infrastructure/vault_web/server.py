"""HTTPS server (stdlib http.server + ssl) for loading vault secrets from a browser.
One GET form + one POST write, gated by a token in the path. Runs in a daemon thread.
Default request logging is suppressed; only redacted lines are emitted, so a token or
secret value can never reach the logs."""
import html
import logging
import ssl
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger("ari.vault_web")


def _render(token: str, names: list[str], have: set[str], stored: list[str]) -> str:
    rows = []
    for n in names:
        badge = "cargado" if n in have else "falta"
        safe = html.escape(n)
        rows.append(f'<label>{safe} <em>({badge})</em><br>'
                    f'<input type="password" name="{safe}" autocomplete="off"></label><br><br>')
    note = ""
    if stored:
        note = f'<p class="ok">Guardado: {html.escape(", ".join(stored))}</p>'
    return (f'<!doctype html><html><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>Bóveda de Ari</title></head><body>'
            f'<h1>Cargar credenciales en la bóveda</h1>{note}'
            f'<form method="post" action="/v/{html.escape(token)}">'
            f'{"".join(rows)}<button type="submit">Guardar</button></form>'
            f'<p>Solo se guardan los campos que completes. Los valores no se muestran.</p>'
            f'</body></html>')


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # suppress default (would log the token)
        pass

    def _token(self):
        parts = [p for p in self.path.split("?", 1)[0].split("/") if p]
        return parts[1] if len(parts) == 2 and parts[0] == "v" else None

    def _reply(self, status: int, body: str, ctype: str = "text/html; charset=utf-8"):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; form-action 'self'; base-uri 'none'",
        )
        self.end_headers()
        self.wfile.write(data)
        log.info("%s /v/<redacted> -> %s", self.command, status)  # redacted

    def do_GET(self):
        token = self._token()
        if token is None:
            return self._reply(404, "no encontrado", "text/plain; charset=utf-8")
        if not self.server.store.valid(token):
            return self._reply(403, "Link vencido o inválido.", "text/plain; charset=utf-8")
        have = set(self.server.vault.names())
        self._reply(200, _render(token, self.server.names, have, []))

    def do_POST(self):
        token = self._token()
        if token is None:
            return self._reply(404, "no encontrado", "text/plain; charset=utf-8")
        if not self.server.store.valid(token):
            return self._reply(403, "Link vencido o inválido.", "text/plain; charset=utf-8")
        length = int(self.headers.get("Content-Length", "0") or "0")
        form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
        stored = []
        for name, values in form.items():
            if name not in self.server.names:
                return self._reply(400, f"Nombre no permitido: {html.escape(name)}",
                                   "text/plain; charset=utf-8")
            value = values[0] if values else ""
            if value:
                self.server.vault.set(name, value)
                stored.append(name)
        have = set(self.server.vault.names())
        self._reply(200, _render(token, self.server.names, have, stored))


class VaultWebServer:
    def __init__(self, bind: str, port: int, cert_path: str, key_path: str,
                 vault, store, names: list[str]):
        self._bind, self._port = bind, port
        self._cert, self._key = cert_path, key_path
        self._vault, self._store, self._names = vault, store, names
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return self._httpd.server_address[1] if self._httpd else self._port

    def start(self) -> None:
        if self._httpd is not None:
            return
        httpd = ThreadingHTTPServer((self._bind, self._port), _Handler)
        httpd.vault, httpd.store, httpd.names = self._vault, self._store, self._names
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(self._cert, self._key)
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
        self._httpd = httpd
        self._thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._httpd is None:
            return
        self._httpd.shutdown()
        self._httpd.server_close()
        self._httpd = None
        self._thread = None
