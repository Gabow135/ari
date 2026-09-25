import asyncio

from ari.domain.ports.progress_port import TEXT, THINKING, TOOL, ProgressEvent, emit_progress
from ari.infrastructure.gateway.progress_message import ProgressMessage


class _FakeBot:
    def __init__(self, fail=False):
        self.sent, self.edits, self.deleted, self.actions = [], [], [], []
        self._fail = fail

    async def send_chat_action(self, chat_id, action):
        self.actions.append(action)

    async def send_message(self, chat_id, text):
        self.sent.append(text)
        return type("M", (), {"message_id": 99})()

    async def edit_message_text(self, text, chat_id, message_id):
        if self._fail:
            raise RuntimeError("Bad Request: message is not modified")
        self.edits.append(text)

    async def delete_message(self, chat_id, message_id):
        if self._fail:
            raise RuntimeError("Bad Request: message can't be deleted")
        self.deleted.append(message_id)


async def _settle():
    await asyncio.sleep(0.15)


async def test_no_events_only_shows_typing():
    bot = _FakeBot()
    async with ProgressMessage(bot, 1, min_interval=0.01):
        await _settle()
    assert bot.actions and bot.actions[0] == "typing"
    assert bot.sent == [] and bot.deleted == []


async def test_steps_and_text_are_shown_then_message_deleted():
    bot = _FakeBot()
    async with ProgressMessage(bot, 1, min_interval=0.01):
        emit_progress(ProgressEvent(THINKING))
        await _settle()
        emit_progress(ProgressEvent(TOOL, "Read", "main.py"))
        emit_progress(ProgressEvent(TEXT, detail="Hola "))
        emit_progress(ProgressEvent(TEXT, detail="mundo"))
        await _settle()
    assert "Pensando" in bot.sent[0]
    last = bot.edits[-1]
    assert "main.py" in last and "Hola mundo" in last
    assert bot.deleted == [99]


async def test_edits_are_throttled():
    bot = _FakeBot()
    async with ProgressMessage(bot, 1, min_interval=0.3):
        for i in range(30):
            emit_progress(ProgressEvent(TEXT, detail=f"{i} "))
            await asyncio.sleep(0.01)
    assert len(bot.sent) + len(bot.edits) <= 3


async def test_events_after_close_are_ignored():
    bot = _FakeBot()
    pm = ProgressMessage(bot, 1, min_interval=0.01)
    async with pm:
        pass
    pm.emit(ProgressEvent(THINKING))
    await _settle()
    assert bot.sent == []


async def test_emit_outside_context_is_noop():
    emit_progress(ProgressEvent(THINKING))  # must not raise


async def test_telegram_errors_never_propagate():
    bot = _FakeBot(fail=True)
    async with ProgressMessage(bot, 1, min_interval=0.01):
        emit_progress(ProgressEvent(THINKING))
        await _settle()
        emit_progress(ProgressEvent(TEXT, detail="x"))
        await _settle()
    assert bot.sent  # sent fine; edit/delete failures swallowed


async def test_long_text_is_trimmed_under_telegram_limit():
    bot = _FakeBot()
    async with ProgressMessage(bot, 1, min_interval=0.01):
        emit_progress(ProgressEvent(TEXT, detail="a" * 10000))
        await _settle()
    assert all(len(t) <= 4096 for t in bot.sent + bot.edits)


async def test_message_sent_while_closing_is_still_deleted():
    bot = _FakeBot()
    real_send = bot.send_message

    async def slow_send(chat_id, text):
        await asyncio.sleep(0.2)
        return await real_send(chat_id, text)

    bot.send_message = slow_send
    async with ProgressMessage(bot, 1, min_interval=0.01):
        emit_progress(ProgressEvent(THINKING))
        await asyncio.sleep(0.05)  # send is in flight when the turn ends
    assert bot.sent and bot.deleted == [99]


async def test_action_blocks_never_shown_while_streaming():
    bot = _FakeBot()
    async with ProgressMessage(bot, 1, min_interval=0.01):
        emit_progress(ProgressEvent(TEXT, detail='Listo <ari-action>{"type":'))
        await _settle()
        emit_progress(ProgressEvent(TEXT, detail='"reminder"}</ari-action> extra'))
        await _settle()
    shown = "\n".join(bot.sent + bot.edits)
    assert "<ari-action>" not in shown
    assert "Listo" in shown


async def test_internal_tools_hidden_and_labels_are_friendly():
    bot = _FakeBot()
    async with ProgressMessage(bot, 1, min_interval=0.01):
        emit_progress(ProgressEvent(TOOL, "ToolSearch", "select:ExitPlanMode"))
        emit_progress(ProgressEvent(TOOL, "Agent", "a long prompt"))
        emit_progress(ProgressEvent(TOOL, "Frobnicate", ""))
        await _settle()
    shown = (bot.sent + bot.edits)[-1]
    assert "ToolSearch" not in shown and "long prompt" not in shown
    assert "subagente" in shown and "🔧 Frobnicate" in shown
