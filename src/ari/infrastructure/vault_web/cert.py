"""Self-signed TLS cert for the LAN vault maintainer, generated with cryptography
(no openssl). Regenerated only when absent or when the LAN IP changed."""
import datetime
import ipaddress
import os

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def ensure_cert(dir_path: str, lan_ip: str) -> tuple[str, str]:
    os.makedirs(dir_path, exist_ok=True)
    cert_path = os.path.join(dir_path, "vault_web_cert.pem")
    key_path = os.path.join(dir_path, "vault_web_key.pem")
    marker = os.path.join(dir_path, "vault_web_cert.ip")
    if (os.path.exists(cert_path) and os.path.exists(key_path)
            and _read(marker) == lan_ip):
        return cert_path, key_path

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "ari-vault")])
    ips = {ipaddress.ip_address("127.0.0.1"), ipaddress.ip_address(lan_ip)}
    san = x509.SubjectAlternativeName(
        [x509.DNSName("localhost")] + [x509.IPAddress(ip) for ip in ips])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=3650))
            .add_extension(san, critical=False)
            .sign(key, hashes.SHA256()))

    key_pem = key.private_bytes(serialization.Encoding.PEM,
                                serialization.PrivateFormat.TraditionalOpenSSL,
                                serialization.NoEncryption())
    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(key_pem)
    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(marker, "w", encoding="utf-8") as f:
        f.write(lan_ip)
    return cert_path, key_path
