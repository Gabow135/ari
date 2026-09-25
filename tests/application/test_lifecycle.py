from ari.application.admin.lifecycle import RESTART, STOP, Lifecycle

OWNER = "42"


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _lifecycle(clock=None):
    return Lifecycle(is_owner=lambda uid: uid == OWNER, clock=clock or _Clock(), ttl=60)


def test_non_owner_is_rejected():
    lc = _lifecycle()
    assert "solo" in lc.request(STOP, "7").lower()
    assert not lc.confirm("dale", "7").handled


def test_request_asks_for_confirmation_without_acting():
    lc = _lifecycle()
    reply = lc.request(RESTART, OWNER)
    assert "dale" in reply.lower()


def test_affirmative_within_ttl_confirms_action():
    lc = _lifecycle()
    lc.request(STOP, OWNER)
    out = lc.confirm("Dale", OWNER)
    assert out.handled and out.action == STOP and out.reply


def test_confirmation_is_single_use():
    lc = _lifecycle()
    lc.request(STOP, OWNER)
    lc.confirm("dale", OWNER)
    assert not lc.confirm("dale", OWNER).handled


def test_negative_cancels():
    lc = _lifecycle()
    lc.request(RESTART, OWNER)
    out = lc.confirm("no", OWNER)
    assert out.handled and out.action is None and "cancel" in out.reply.lower()
    assert not lc.confirm("dale", OWNER).handled


def test_other_text_cancels_and_is_consumed():
    lc = _lifecycle()
    lc.request(RESTART, OWNER)
    out = lc.confirm("qué hora es", OWNER)
    assert out.handled and out.action is None


def test_expired_affirmative_is_rejected():
    clock = _Clock()
    lc = _lifecycle(clock)
    lc.request(STOP, OWNER)
    clock.now += 61
    out = lc.confirm("dale", OWNER)
    assert out.handled and out.action is None and "venci" in out.reply.lower()


def test_expired_non_affirmative_falls_through():
    clock = _Clock()
    lc = _lifecycle(clock)
    lc.request(STOP, OWNER)
    clock.now += 61
    assert not lc.confirm("hola", OWNER).handled


def test_no_pending_is_not_handled():
    assert not _lifecycle().confirm("dale", OWNER).handled
