from dataclasses import dataclass
from datetime import datetime, timezone

from ari.application.whatsapp.filter import WhatsAppFilter
from ari.domain.whatsapp.entities import InboundWhatsApp


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class Row:
    id: int
    wa_chat_id: str
    contact_name: str
    text: str
    media_kind: str
    ts: str
    inbound_id: int | None = None


class SqliteWhatsApp:
    def __init__(self, conn, clock=_utcnow):
        self._c = conn
        self._clock = clock

    async def record_inbound(self, m: InboundWhatsApp) -> int:
        cur = await self._c.execute(
            "INSERT INTO whatsapp_messages "
            "(wa_chat_id, contact_name, direction, text, media_kind, status, ts) "
            "VALUES (?,?, 'in', ?,?, 'pending', ?)",
            (m.wa_chat_id, m.contact_name, m.text, m.media_kind, self._clock().isoformat()),
        )
        await self._c.commit()
        return cur.lastrowid

    async def mark_notified(self, id: int) -> None:
        await self._c.execute(
            "UPDATE whatsapp_messages SET status='notified' WHERE id=? AND status='pending'", (id,)
        )
        await self._c.commit()

    async def list_pending(self, limit: int) -> list[Row]:
        cur = await self._c.execute(
            "SELECT id, wa_chat_id, contact_name, text, media_kind, ts FROM whatsapp_messages "
            "WHERE direction='in' AND status IN ('pending','notified') ORDER BY id DESC LIMIT ?",
            (max(1, min(limit, 25)),),
        )
        return [Row(*r) for r in await cur.fetchall()]

    async def get_inbound(self, id: int) -> Row | None:
        cur = await self._c.execute(
            "SELECT id, wa_chat_id, contact_name, text, media_kind, ts FROM whatsapp_messages "
            "WHERE id=? AND direction='in'",
            (id,),
        )
        r = await cur.fetchone()
        return Row(*r) if r else None

    async def create_draft(
        self, inbound_id: int, wa_chat_id: str, contact_name: str, text: str
    ) -> int:
        cur = await self._c.execute(
            "INSERT INTO whatsapp_messages "
            "(inbound_id, wa_chat_id, contact_name, direction, text, status, ts) "
            "VALUES (?,?,?, 'out', ?, 'draft', ?)",
            (inbound_id, wa_chat_id, contact_name, text, self._clock().isoformat()),
        )
        await self._c.commit()
        return cur.lastrowid

    async def get_draft(self, draft_id: int) -> Row | None:
        cur = await self._c.execute(
            "SELECT id, wa_chat_id, contact_name, text, media_kind, ts, inbound_id "
            "FROM whatsapp_messages WHERE id=? AND direction='out' AND status='draft'",
            (draft_id,),
        )
        r = await cur.fetchone()
        return Row(r[0], r[1], r[2], r[3], r[4], r[5], r[6]) if r else None

    async def queue_draft(self, draft_id: int) -> Row | None:
        cur = await self._c.execute(
            "UPDATE whatsapp_messages SET status='queued' WHERE id=? AND status='draft'",
            (draft_id,),
        )
        if cur.rowcount == 0:
            return None
        row = await self._row(draft_id)
        if row.inbound_id is not None:
            await self._c.execute(
                "UPDATE whatsapp_messages SET status='answered' WHERE id=? AND direction='in'",
                (row.inbound_id,),
            )
        await self._c.commit()
        return row

    async def claim_queued(self) -> Row | None:
        # Step 1: find the candidate id
        # single-connection assumption: aiosqlite serializes coroutines on one conn
        cur = await self._c.execute(
            "SELECT id FROM whatsapp_messages WHERE direction='out' AND status='queued' "
            "ORDER BY id LIMIT 1"
        )
        r = await cur.fetchone()
        if r is None:
            return None
        candidate_id = r[0]

        # Step 2: atomically claim exactly that row
        cur2 = await self._c.execute(
            "UPDATE whatsapp_messages SET status='sending' WHERE id=? AND status='queued'",
            (candidate_id,),
        )
        await self._c.commit()
        if cur2.rowcount == 0:
            # Another caller won the race
            return None

        # Step 3: return the row we just claimed by exact id
        return await self._row(candidate_id)

    async def mark_sent(self, id: int) -> None:
        await self._c.execute(
            "UPDATE whatsapp_messages SET status='sent' WHERE id=?", (id,)
        )
        await self._c.commit()

    async def mark_failed(self, id: int) -> None:
        await self._c.execute(
            "UPDATE whatsapp_messages SET status='failed' WHERE id=?", (id,)
        )
        await self._c.commit()

    async def reset_sending(self) -> int:
        """Re-queue outbound rows stuck in 'sending' (e.g. after a crash).

        Returns the number of rows recovered.
        """
        cur = await self._c.execute(
            "UPDATE whatsapp_messages SET status='queued' "
            "WHERE direction='out' AND status='sending'"
        )
        await self._c.commit()
        return cur.rowcount

    async def get_filter(self) -> WhatsAppFilter:
        cur = await self._c.execute("SELECT kind, value FROM whatsapp_filter")
        contacts, keywords = set(), set()
        for kind, value in await cur.fetchall():
            (contacts if kind == "contact" else keywords).add(value)
        return WhatsAppFilter(frozenset(contacts), frozenset(keywords))

    async def add_contact(self, v: str) -> None:
        await self._put("contact", v)

    async def remove_contact(self, v: str) -> None:
        await self._del("contact", v)

    async def add_keyword(self, v: str) -> None:
        await self._put("keyword", v)

    async def remove_keyword(self, v: str) -> None:
        await self._del("keyword", v)

    async def _put(self, kind: str, v: str) -> None:
        await self._c.execute(
            "INSERT OR IGNORE INTO whatsapp_filter (kind, value) VALUES (?,?)", (kind, v.strip())
        )
        await self._c.commit()

    async def _del(self, kind: str, v: str) -> None:
        await self._c.execute(
            "DELETE FROM whatsapp_filter WHERE kind=? AND value=?", (kind, v.strip())
        )
        await self._c.commit()

    async def _row(self, id: int) -> Row:
        cur = await self._c.execute(
            "SELECT id, wa_chat_id, contact_name, text, media_kind, ts, inbound_id "
            "FROM whatsapp_messages WHERE id=?",
            (id,),
        )
        r = await cur.fetchone()
        if r is None:
            raise AssertionError(f"_row: no row for id={id}")
        return Row(r[0], r[1], r[2], r[3], r[4], r[5], r[6])
