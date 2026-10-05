"""Native IMAP reader (stdlib only). Blocking; callers run it off the loop."""
import email
import imaplib
from email.header import decode_header, make_header

from ari.domain.email.entities import EmailSummary, MailboxSpec

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
            _typ, data = conn.uid("SEARCH", None, *_imap_criteria(criteria))
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
