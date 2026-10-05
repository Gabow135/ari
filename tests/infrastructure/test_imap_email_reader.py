import email.message

import pytest

from ari.domain.email.entities import MailboxSpec
from ari.infrastructure.email.imap_email_reader import ImapEmailReader, _imap_criteria, _parse_parts

SPEC = MailboxSpec("email_corp", "imap.x.com", 993, True, "u@x.com", "pw")

_HDR = (b"From: =?UTF-8?Q?Pagos_Mrjoy?= <pay@mrjoy.com>\r\n"
        b"Subject: =?UTF-8?Q?Factura_octubre?=\r\n"
        b"Date: Mon, 5 Oct 2026 10:00:00 -0500\r\n\r\n")


class FakeConn:
    def __init__(self):
        self.logged_out = False
        self.selected = None
    def login(self, user, pw): self.user = user
    def select(self, mailbox, readonly=False): self.selected = (mailbox, readonly)
    def uid(self, command, *args):
        if command == "SEARCH":
            return "OK", [b"1 2 3"]
        if command == "FETCH":
            return "OK", [(args[0] + b" (HDR", _HDR)]
        return "OK", [b""]
    def logout(self): self.logged_out = True


def test_criteria_mapping():
    assert _imap_criteria("") == ["ALL"]
    assert _imap_criteria("de:pay@mrjoy.com") == ["FROM", '"pay@mrjoy.com"']
    assert _imap_criteria("asunto:factura") == ["SUBJECT", '"factura"']
    assert _imap_criteria("no leidos") == ["UNSEEN"]
    assert _imap_criteria("desde:2026-10-01") == ["SINCE", "01-Oct-2026"]
    assert _imap_criteria("saldo total") == ["TEXT", '"saldo total"']


def test_search_returns_newest_first_and_decodes_headers():
    conn = FakeConn()
    reader = ImapEmailReader(ssl_factory=lambda *a, **k: conn)
    out = reader.search(SPEC, "", 2)
    assert [s.uid for s in out] == ["3", "2"]  # newest first, limited to 2
    assert out[0].from_addr == "Pagos Mrjoy <pay@mrjoy.com>"
    assert out[0].subject == "Factura octubre"
    assert conn.selected == ("INBOX", True)
    assert conn.logged_out is True


def test_search_closes_connection_on_login_failure():
    import imaplib

    class LoginFailConn:
        def __init__(self):
            self.logged_out = False
        def login(self, user, pw):
            raise imaplib.IMAP4.error("auth failed")
        def select(self, *a, **k):
            pass
        def uid(self, *a):
            return "OK", [b""]
        def logout(self):
            self.logged_out = True

    conn = LoginFailConn()
    reader = ImapEmailReader(ssl_factory=lambda *a, **k: conn)
    import pytest
    with pytest.raises(Exception):
        reader.search(SPEC, "", 5)
    assert conn.logged_out is True


def _build_multipart() -> bytes:
    msg = email.message.EmailMessage()
    msg["From"] = "=?UTF-8?Q?Pagos_Mrjoy?= <pay@mrjoy.com>"
    msg["To"] = "me@corp.com"
    msg["Subject"] = "Factura"
    msg["Date"] = "Mon, 5 Oct 2026 10:00:00 -0500"
    msg.set_content("Total: 1.234 USD")
    msg.add_alternative("<p>Total: <b>1.234</b> USD</p>", subtype="html")
    msg.add_attachment(b"%PDF-1.4 bytes", maintype="application",
                       subtype="pdf", filename="factura.pdf")
    return msg.as_bytes()


class FetchConn:
    def __init__(self, raw): self._raw = raw; self.logged_out = False
    def login(self, u, p): pass
    def select(self, m, readonly=False): pass
    def uid(self, command, *args):
        if command == "FETCH" and self._raw is not None:
            return "OK", [(args[0] + b" (RFC822", self._raw)]
        return "OK", [None]
    def logout(self): self.logged_out = True


def test_fetch_parses_body_and_attachment():
    from ari.infrastructure.email.imap_email_reader import ImapEmailReader
    conn = FetchConn(_build_multipart())
    reader = ImapEmailReader(ssl_factory=lambda *a, **k: conn)
    msg = reader.fetch(SPEC, "7")
    assert msg.uid == "7"
    assert msg.from_addr == "Pagos Mrjoy <pay@mrjoy.com>"
    assert "Total: 1.234 USD" in msg.body_text  # prefers text/plain
    assert len(msg.attachments) == 1
    assert msg.attachments[0].filename == "factura.pdf"
    assert msg.attachments[0].data == b"%PDF-1.4 bytes"
    assert conn.logged_out is True


def test_fetch_missing_uid_raises_lookup():
    from ari.infrastructure.email.imap_email_reader import ImapEmailReader
    reader = ImapEmailReader(ssl_factory=lambda *a, **k: FetchConn(None))
    with pytest.raises(LookupError):
        reader.fetch(SPEC, "999")


def test_parse_parts_falls_back_to_html_when_no_plain():
    msg = email.message.EmailMessage()
    msg["Subject"] = "x"
    msg.set_content("<div>Hola &amp; chau</div>", subtype="html")
    body, atts = _parse_parts(msg)
    assert "Hola & chau" in body and atts == []
