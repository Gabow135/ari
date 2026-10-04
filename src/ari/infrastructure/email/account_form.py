import importlib.resources as res

PROVIDERS = {
    "gmail":   {"imap_host": "imap.gmail.com", "imap_port": 993, "imap_secure": True,
                "smtp_host": "smtp.gmail.com", "smtp_port": 465, "smtp_secure": True},
    "outlook": {"imap_host": "outlook.office365.com", "imap_port": 993, "imap_secure": True,
                "smtp_host": "smtp.office365.com", "smtp_port": 587, "smtp_secure": False},
    "corp":    {"imap_host": "", "imap_port": 993, "imap_secure": True,
                "smtp_host": "", "smtp_port": 465, "smtp_secure": True},
    "custom":  {"imap_host": "", "imap_port": 993, "imap_secure": True,
                "smtp_host": "", "smtp_port": 465, "smtp_secure": True},
}


def _sodium_js() -> str:
    return res.files("ari.infrastructure.email.assets").joinpath("sodium.js").read_text()


def render_enroll_html(public_key_b64: str) -> str:
    import json
    presets = json.dumps(PROVIDERS)
    sodium = _sodium_js()
    # The submit handler builds the account JSON, seals it to Ari's public key,
    # and shows `ari-mail:v1:<base64 ORIGINAL>`. All client-side, no network.
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Conectar correo a Ari</title></head><body>
<h1>Conectá tu correo</h1>
<p>Esto se cifra en tu dispositivo. Pegale el código a Ari cuando termines.</p>
<form id="f">
  <label>Proveedor <select id="provider"></select></label>
  <label>Etiqueta (ej. trabajo) <input name="label" required></label>
  <label>IMAP host <input name="imap_host" required></label>
  <label>IMAP puerto <input name="imap_port" type="number" value="993"></label>
  <label>SMTP host <input name="smtp_host" required></label>
  <label>SMTP puerto <input name="smtp_port" type="number" value="465"></label>
  <label>Correo / usuario <input name="email_user" required></label>
  <label>Contraseña (o App Password) <input name="password" type="password" required></label>
  <button type="submit">Generar código</button>
</form>
<textarea id="out" readonly rows="4" style="width:100%"></textarea>
<button id="copy" type="button">Copiar</button>
<script>{sodium}</script>
<script>
const PUB = "{public_key_b64}";
const PRESETS = {presets};
const sel = document.getElementById('provider');
Object.keys(PRESETS).forEach(k => {{ const o=document.createElement('option'); o.value=k; o.textContent=k; sel.appendChild(o); }});
function applyPreset() {{ const p = PRESETS[sel.value]; const f = document.getElementById('f');
  for (const k of ['imap_host','imap_port','smtp_host','smtp_port']) if (p[k] !== "") f[k].value = p[k]; }}
sel.addEventListener('change', applyPreset);
document.getElementById('f').addEventListener('submit', async (e) => {{
  e.preventDefault();
  await sodium.ready;
  const f = e.target;
  const acct = {{
    label: f.label.value.trim().toLowerCase(),
    imap_host: f.imap_host.value.trim(), imap_port: parseInt(f.imap_port.value, 10),
    imap_secure: parseInt(f.imap_port.value,10) !== 143,
    smtp_host: f.smtp_host.value.trim(), smtp_port: parseInt(f.smtp_port.value, 10),
    smtp_secure: parseInt(f.smtp_port.value,10) === 465,
    email_user: f.email_user.value.trim(), password: f.password.value,
  }};
  const pub = sodium.from_base64(PUB, sodium.base64_variants.ORIGINAL);
  const sealed = sodium.crypto_box_seal(JSON.stringify(acct), pub);
  document.getElementById('out').value = "ari-mail:v1:" + sodium.to_base64(sealed, sodium.base64_variants.ORIGINAL);
}});
document.getElementById('copy').addEventListener('click', () => {{
  const o = document.getElementById('out'); o.select(); document.execCommand('copy'); }});
</script></body></html>"""
