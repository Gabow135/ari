import pytest

from ari.domain.email.entities import (EmailAttachment, EmailSummary,
                                        FetchedEmail, MailboxSpec)


def test_mailbox_spec_is_frozen():
    spec = MailboxSpec("email_corp", "imap.x.com", 993, True, "u@x.com", "secret")
    assert spec.cuenta == "email_corp" and spec.imap_port == 993
    with pytest.raises(Exception):
        spec.password = "other"  # frozen dataclass


def test_fetched_email_holds_attachments():
    att = EmailAttachment("factura.pdf", "application/pdf", b"%PDF-1.4")
    msg = FetchedEmail("12", "a@x.com", "b@y.com", "Pago", "Mon, 5 Oct",
                       "cuerpo", (att,))
    assert msg.attachments[0].filename == "factura.pdf"
    assert EmailSummary("12", "a@x.com", "Pago", "Mon, 5 Oct", "snip").uid == "12"
