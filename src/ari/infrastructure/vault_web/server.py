"""HTTPS server (stdlib http.server + ssl) for loading vault secrets from a browser.
One GET form + one POST write, gated by a token in the path. Runs in a daemon thread.
Default request logging is suppressed; only redacted lines are emitted, so a token or
secret value can never reach the logs."""
import base64
import hashlib
import html
import logging
import ssl
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger("ari.vault_web")


# Futuristic theme. No external resources, so the strict CSP keeps img-src/font-src
# denied; only inline styles and the one hash-pinned script below are allowed.
_STYLE = """
:root{
  --bg:#05060d; --cyan:#22d3ee; --violet:#a855f7; --ink:#e8ecff;
  --muted:#8b93c4; --ok:#34d399; --warn:#fbbf24;
}
*{box-sizing:border-box}
html,body{margin:0;min-height:100%}
body{
  font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace;
  color:var(--ink); background:var(--bg); overflow-x:hidden;
  display:flex;align-items:center;justify-content:center;
  padding:28px 16px;min-height:100vh;
}
body::before{
  content:"";position:fixed;inset:-40%;z-index:-1;
  background:
    radial-gradient(42% 42% at 18% 22%,rgba(34,211,238,.22),transparent 60%),
    radial-gradient(45% 45% at 82% 18%,rgba(168,85,247,.22),transparent 60%),
    radial-gradient(55% 55% at 50% 95%,rgba(56,189,248,.14),transparent 60%);
  filter:blur(8px);animation:drift 18s ease-in-out infinite alternate;
}
@keyframes drift{
  0%{transform:translate(0,0) scale(1)}
  100%{transform:translate(0,-4%) scale(1.08)}
}
.card{
  position:relative;width:100%;max-width:460px;padding:30px 26px 24px;
  border-radius:20px;background:rgba(13,16,34,.72);
  border:1px solid rgba(120,140,255,.18);
  box-shadow:0 24px 70px rgba(0,0,0,.55),inset 0 1px 0 rgba(255,255,255,.05);
  backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);
  animation:rise .6s cubic-bezier(.2,.8,.2,1) both;
}
.card::before{
  content:"";position:absolute;inset:-1px;border-radius:21px;z-index:-1;
  background:linear-gradient(130deg,var(--cyan),var(--violet),var(--cyan));
  background-size:300% 300%;opacity:.5;filter:blur(7px);
  animation:flow 9s linear infinite;
}
@keyframes flow{0%{background-position:0% 50%}100%{background-position:300% 50%}}
@keyframes rise{from{opacity:0;transform:translateY(14px)}to{opacity:1;transform:none}}
.brand{display:flex;align-items:center;gap:12px;margin-bottom:4px}
.brand svg{width:40px;height:40px;filter:drop-shadow(0 0 10px rgba(34,211,238,.6))}
h1{
  font-size:1.32rem;line-height:1.2;margin:0;letter-spacing:.5px;
  background:linear-gradient(90deg,#fff,var(--cyan),var(--violet));
  background-size:200% auto;-webkit-background-clip:text;background-clip:text;
  color:transparent;animation:shimmer 6s linear infinite;
}
@keyframes shimmer{to{background-position:200% center}}
.sub{color:var(--muted);font-size:.8rem;margin:2px 0 4px;letter-spacing:.3px}
.stats{font-size:.72rem;color:var(--muted);margin:0 0 18px;letter-spacing:.3px}
.stats b{color:var(--cyan)}
.ok{
  display:flex;align-items:center;gap:8px;margin:0 0 16px;padding:11px 14px;
  border-radius:12px;font-size:.82rem;color:#d7fbe9;
  background:rgba(52,211,153,.12);border:1px solid rgba(52,211,153,.4);
  box-shadow:0 0 22px rgba(52,211,153,.18);animation:rise .4s both;
}
.ok::before{content:"\\2713";font-weight:700;color:var(--ok)}
.search{position:relative;margin-bottom:10px}
.search svg{position:absolute;left:13px;top:50%;transform:translateY(-50%);
  width:16px;height:16px;opacity:.55;pointer-events:none}
.search input{padding-left:40px;letter-spacing:.3px}
.chips{display:flex;gap:8px;margin-bottom:16px}
.chip{
  flex:1;padding:8px 6px;border-radius:10px;font:inherit;font-size:.7rem;
  letter-spacing:.6px;text-transform:uppercase;cursor:pointer;color:var(--muted);
  background:rgba(8,11,26,.6);border:1px solid rgba(120,140,255,.16);
  transition:color .2s,background .2s,box-shadow .2s,border-color .2s;
}
.chip.on{color:#03111a;border-color:transparent;
  background:linear-gradient(120deg,var(--cyan),var(--violet));
  box-shadow:0 6px 18px rgba(34,211,238,.25)}
.list{display:flex;flex-direction:column;gap:10px}
.field{
  border:1px solid rgba(120,140,255,.14);border-radius:13px;overflow:hidden;
  background:rgba(8,11,26,.5);transition:border-color .2s,box-shadow .2s;
}
.field:hover{border-color:rgba(120,140,255,.32)}
.field.open{border-color:rgba(34,211,238,.45);box-shadow:0 0 24px rgba(34,211,238,.1)}
.rowhead{
  width:100%;display:flex;align-items:center;gap:10px;padding:13px 14px;
  background:none;border:0;color:var(--ink);font:inherit;cursor:pointer;text-align:left;
}
.rowhead .name{flex:1;font-size:.8rem;letter-spacing:.3px;word-break:break-all;color:#c7cdf5}
.pill{
  font-size:.6rem;text-transform:uppercase;letter-spacing:.8px;
  padding:3px 9px;border-radius:999px;white-space:nowrap;flex:none;
}
.pill.loaded{color:var(--ok);background:rgba(52,211,153,.12);border:1px solid rgba(52,211,153,.45)}
.pill.missing{color:var(--warn);background:rgba(251,191,36,.1);border:1px solid rgba(251,191,36,.4)}
.chev{color:var(--muted);font-size:1.15rem;line-height:1;transition:transform .2s}
.field.open .chev{transform:rotate(90deg);color:var(--cyan)}
.field .body{max-height:0;opacity:0;overflow:hidden;padding:0 14px;
  transition:max-height .28s ease,opacity .2s,padding .28s}
.field.open .body{max-height:140px;opacity:1;padding:2px 14px 14px}
input{
  width:100%;padding:13px 14px;border-radius:11px;color:var(--ink);
  font:inherit;letter-spacing:2px;
  background:rgba(4,6,16,.7);border:1px solid rgba(120,140,255,.2);
  transition:border-color .2s,box-shadow .2s,background .2s;outline:none;
}
input::placeholder{color:#4b5280;letter-spacing:normal}
input:focus{
  border-color:var(--cyan);background:rgba(4,8,20,.9);
  box-shadow:0 0 0 3px rgba(34,211,238,.18),0 0 24px rgba(34,211,238,.25);
}
.noresults{display:none;text-align:center;color:var(--muted);font-size:.8rem;padding:20px 0}
button[type=submit]{
  width:100%;margin-top:16px;padding:14px;border:0;border-radius:12px;
  font:inherit;font-weight:700;letter-spacing:1px;color:#03111a;cursor:pointer;
  background:linear-gradient(120deg,var(--cyan),var(--violet));
  background-size:200% auto;box-shadow:0 10px 30px rgba(34,211,238,.3);
  transition:transform .15s,box-shadow .2s,background-position .4s;
}
button[type=submit]:hover{transform:translateY(-2px);background-position:right center;
  box-shadow:0 14px 40px rgba(168,85,247,.42)}
button[type=submit]:active{transform:translateY(0)}
.foot{margin:18px 0 0;font-size:.72rem;color:var(--muted);text-align:center;line-height:1.5}
"""

# One tiny inline script: live filter + chip tabs + expand-to-edit. It is pinned in
# the CSP by its sha256 hash (computed below), so ONLY these exact bytes may run —
# an injected or altered script is still blocked. No inline on* handlers (a hash
# does not cover those); every listener is attached here.
_SCRIPT = """
(function(){
  var q=document.getElementById('q');
  var list=document.getElementById('list');
  var none=document.getElementById('none');
  var fields=[].slice.call(list.querySelectorAll('.field'));
  var chips=[].slice.call(document.querySelectorAll('.chip'));
  var mode='all';
  function apply(){
    var t=(q.value||'').toLowerCase().trim(),shown=0;
    fields.forEach(function(f){
      var name=f.getAttribute('data-name');
      var loaded=f.getAttribute('data-loaded')==='1';
      var okText=!t||name.indexOf(t)!==-1;
      var okMode=mode==='all'||(mode==='loaded'&&loaded)||(mode==='missing'&&!loaded);
      var vis=okText&&okMode;
      f.style.display=vis?'':'none';
      if(vis)shown++;
    });
    none.style.display=shown?'none':'block';
  }
  q.addEventListener('input',apply);
  chips.forEach(function(c){
    c.addEventListener('click',function(){
      chips.forEach(function(x){x.classList.remove('on')});
      c.classList.add('on');
      mode=c.getAttribute('data-mode');
      apply();
    });
  });
  list.addEventListener('click',function(e){
    var head=e.target.closest('.rowhead');
    if(!head)return;
    var f=head.parentNode;
    f.classList.toggle('open');
    if(f.classList.contains('open')){
      var inp=f.querySelector('input');
      if(inp)setTimeout(function(){inp.focus()},80);
    }
  });
})();
"""

_SCRIPT_HASH = base64.b64encode(hashlib.sha256(_SCRIPT.encode("utf-8")).digest()).decode()

_LOGO = (
    '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true">'
    '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
    '<stop offset="0" stop-color="#22d3ee"/><stop offset="1" stop-color="#a855f7"/>'
    '</linearGradient></defs>'
    '<path d="M12 2.5 4.5 6v6c0 4.6 3.2 7.6 7.5 9.5 4.3-1.9 7.5-4.9 7.5-9.5V6L12 2.5Z" '
    'stroke="url(#g)" stroke-width="1.5" stroke-linejoin="round"/>'
    '<rect x="9" y="11" width="6" height="5.2" rx="1.2" stroke="url(#g)" stroke-width="1.4"/>'
    '<path d="M10.2 11V9.6a1.8 1.8 0 0 1 3.6 0V11" stroke="url(#g)" stroke-width="1.4"/>'
    "</svg>"
)

_SEARCH_ICON = (
    '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true">'
    '<circle cx="11" cy="11" r="7" stroke="#8b93c4" stroke-width="1.8"/>'
    '<path d="m20 20-3.2-3.2" stroke="#8b93c4" stroke-width="1.8" stroke-linecap="round"/>'
    "</svg>"
)


def _render(token: str, names: list[str], have: set[str], stored: list[str]) -> str:
    rows = []
    for n in names:
        loaded = n in have
        klass, badge = ("loaded", "cargado") if loaded else ("missing", "falta")
        safe = html.escape(n)
        placeholder = "nuevo valor para reemplazar" if loaded else "pegá el valor acá"
        rows.append(
            f'<div class="field" data-name="{safe.lower()}" data-loaded="{int(loaded)}">'
            f'<button type="button" class="rowhead">'
            f'<span class="name">{safe}</span>'
            f'<span class="pill {klass}">{badge}</span>'
            f'<span class="chev">›</span></button>'
            f'<div class="body"><input type="password" name="{safe}" '
            f'autocomplete="off" placeholder="{placeholder}"></div></div>'
        )
    note = ""
    if stored:
        note = f'<div class="ok">Guardado: {html.escape(", ".join(stored))}</div>'
    loaded_count = sum(1 for n in names if n in have)
    total = len(names)
    return (
        f'<!doctype html><html lang="es"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>Bóveda de Ari</title><style>{_STYLE}</style></head><body>'
        f'<main class="card"><div class="brand">{_LOGO}'
        f'<h1>Bóveda de Ari</h1></div>'
        f'<p class="sub">Tocá una credencial para cargarla o actualizarla. '
        f'Viaje cifrado, solo en tu red.</p>'
        f'<p class="stats"><b>{loaded_count}</b> de <b>{total}</b> cargadas</p>'
        f'{note}'
        f'<div class="search">{_SEARCH_ICON}'
        f'<input type="search" id="q" autocomplete="off" '
        f'placeholder="Buscar credencial…"></div>'
        f'<div class="chips">'
        f'<button type="button" class="chip on" data-mode="all">Todas</button>'
        f'<button type="button" class="chip" data-mode="loaded">Cargadas</button>'
        f'<button type="button" class="chip" data-mode="missing">Faltan</button></div>'
        f'<form method="post" action="/v/{html.escape(token)}">'
        f'<div class="list" id="list">{"".join(rows)}</div>'
        f'<p class="noresults" id="none">Sin resultados.</p>'
        f'<button type="submit">Guardar en la bóveda</button></form>'
        f'<p class="foot">Solo se guardan los campos que completes.<br>'
        f'Los valores nunca se muestran ni quedan en el historial.</p>'
        f'<script>{_SCRIPT}</script>'
        f'</main></body></html>'
    )


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
            "default-src 'none'; style-src 'unsafe-inline'; "
            f"script-src 'sha256-{_SCRIPT_HASH}'; form-action 'self'; base-uri 'none'",
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

    def set_names(self, names: list[str]) -> None:
        self._names = names
        if self._httpd is not None:
            self._httpd.names = names

    def stop(self) -> None:
        if self._httpd is None:
            return
        self._httpd.shutdown()
        self._httpd.server_close()
        self._httpd = None
        self._thread = None
