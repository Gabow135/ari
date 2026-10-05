"""Native IMAP reader (stdlib only). Blocking; callers run it off the loop."""
import email
import imaplib
from email.header import decode_header, make_header

from ari.domain.email.entities import EmailAttachment, EmailSummary, FetchedEmail, MailboxSpec
from ari.infrastructure.email.html_text import html_to_text

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:  # noqa: BLE001 — malformed header: fall back to raw
        return value


def _q(value: str) -> str:
    return '"' + value.replace('"', "") + '"'


def _imap_date(value: str) -> str:
    year, month, day = value.split("-")
    return f"{int(day):02d}-{_MONTHS[int(month) - 1]}-{year}"


def _imap_criteria(criteria: str) -> list[str]:
    text = (criteria or "").strip()
    if not text:
        return ["ALL"]
    low = text.lower()
    if low.startswith("de:"):
        return ["FROM", _q(text[3:].strip())]
    if low.startswith("asunto:"):
        return ["SUBJECT", _q(text[7:].strip())]
    if low.startswith("desde:"):
        try:
            return ["SINCE", _imap_date(text[6:].strip())]
        except (ValueError, IndexError):
            return ["ALL"]
    if low in ("no leidos", "no leídos", "nuevos", "unseen"):
        return ["UNSEEN"]
    return ["TEXT", _q(text)]


def _logout(conn) -> None:
    try:
        conn.logout()
    except Exception:  # noqa: BLE001, S110 — best-effort close
        pass


def _part_text(part) -> str:
    payload = part.get_payload(decode=True) or b""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, "replace")
    except LookupError:
        return payload.decode("utf-8", "replace")


def _parse_parts(msg) -> tuple[str, list[EmailAttachment]]:
    body_plain, body_html, attachments = "", "", []
    parts = msg.walk() if msg.is_multipart() else [msg]
    for part in parts:
        if part.is_multipart():
            continue
        disposition = (part.get("Content-Disposition") or "").lower()
        filename = part.get_filename()
        ctype = part.get_content_type()
        if filename or "attachment" in disposition:
            attachments.append(EmailAttachment(
                _decode(filename) or "adjunto", ctype,
                part.get_payload(decode=True) or b""))
        elif ctype == "text/plain" and not body_plain:
            body_plain = _part_text(part)
        elif ctype == "text/html" and not body_html:
            body_html = _part_text(part)
    body = body_plain or html_to_text(body_html)
    return body.strip(), attachments


class ImapEmailReader:
    def __init__(self, *, timeout: float = 20.0,
                 ssl_factory=imaplib.IMAP4_SSL, plain_factory=imaplib.IMAP4):
        self._timeout = timeout
        self._ssl_factory = ssl_factory
        self._plain_factory = plain_factory

    def _connect(self, spec: MailboxSpec):
        if spec.imap_secure:
            conn = self._ssl_factory(spec.imap_host, spec.imap_port,
                                     timeout=self._timeout)
        else:
            conn = self._plain_factory(spec.imap_host, spec.imap_port,
                                       timeout=self._timeout)
        try:
            if not spec.imap_secure:
                conn.starttls()
            conn.login(spec.user, spec.password)
        except BaseException:
            _logout(conn)
            raise
        return conn

    def search(self, spec: MailboxSpec, criteria: str,
               limit: int) -> list[EmailSummary]:
        conn = self._connect(spec)
        try:
            conn.select("INBOX", readonly=True)
            crit = _imap_criteria(criteria)
            if all(token.isascii() for token in crit):
                _typ, data = conn.uid("SEARCH", None, *crit)
            else:
                # Non-ASCII terms require CHARSET UTF-8; send the accented tokens
                # as UTF-8 byte literals while keeping ASCII keywords as atoms.
                encoded = [t if t.isascii() else t.encode("utf-8") for t in crit]
                _typ, data = conn.uid("SEARCH", "CHARSET", "UTF-8", *encoded)
            uids = data[0].split() if data and data[0] else []
            uids = uids[-limit:][::-1]  # newest UID first
            return [self._summary(conn, uid) for uid in uids]
        finally:
            _logout(conn)

    def _summary(self, conn, uid: bytes) -> EmailSummary:
        _typ, data = conn.uid(
            "FETCH", uid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
        raw = data[0][1] if data and data[0] and isinstance(data[0], tuple) else b""
        msg = email.message_from_bytes(raw)
        return EmailSummary(
            uid=uid.decode(), from_addr=_decode(msg.get("From")),
            subject=_decode(msg.get("Subject")), date=_decode(msg.get("Date")),
            snippet="")

    def fetch(self, spec: MailboxSpec, uid: str) -> FetchedEmail:
        conn = self._connect(spec)
        try:
            conn.select("INBOX", readonly=True)
            _typ, data = conn.uid("FETCH", uid.encode(), "(RFC822)")
            if not data or not data[0] or not isinstance(data[0], tuple):
                raise LookupError(uid)
            msg = email.message_from_bytes(data[0][1])
            body, attachments = _parse_parts(msg)
            return FetchedEmail(
                uid=uid, from_addr=_decode(msg.get("From")),
                to_addr=_decode(msg.get("To")),
                subject=_decode(msg.get("Subject")),
                date=_decode(msg.get("Date")), body_text=body,
                attachments=tuple(attachments))
        finally:
            _logout(conn)
