import ipaddress
import os

from cryptography import x509

from ari.infrastructure.vault_web.cert import ensure_cert


def _sans(cert_path):
    cert = x509.load_pem_x509_certificate(open(cert_path, "rb").read())
    ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    return set(ext.get_values_for_type(x509.IPAddress))


def test_generates_cert_and_key_with_ip_san(tmp_path):
    c, k = ensure_cert(str(tmp_path), "192.168.1.50")
    assert os.path.exists(c) and os.path.exists(k)
    assert (os.stat(k).st_mode & 0o777) == 0o600
    assert ipaddress.ip_address("192.168.1.50") in _sans(c)


def test_reuses_existing_cert_for_same_ip(tmp_path):
    c1, _ = ensure_cert(str(tmp_path), "192.168.1.50")
    m1 = os.stat(c1).st_mtime_ns
    c2, _ = ensure_cert(str(tmp_path), "192.168.1.50")
    assert os.stat(c2).st_mtime_ns == m1  # not regenerated


def test_regenerates_when_ip_changes(tmp_path):
    c, _ = ensure_cert(str(tmp_path), "192.168.1.50")
    ensure_cert(str(tmp_path), "10.0.0.9")
    assert ipaddress.ip_address("10.0.0.9") in _sans(c)
