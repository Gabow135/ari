from ari.application.whatsapp.filter import WhatsAppFilter, passes_filter


def _f(contacts=(), keywords=()):
    return WhatsAppFilter(frozenset(contacts), frozenset(keywords))


def test_contact_match_is_case_insensitive():
    assert passes_filter("Juan Perez", "549111@s.whatsapp.net", "hola", _f(contacts={"juan perez"}))


def test_contact_match_by_number():
    assert passes_filter("", "549111@s.whatsapp.net", "hola", _f(contacts={"549111"}))


def test_keyword_match_ignores_accents_and_case():
    assert passes_filter("Ana", "x@s.whatsapp.net", "Ya te Pagó el flete", _f(keywords={"pago"}))


def test_no_match_returns_false():
    assert not passes_filter("Ana", "x@s.whatsapp.net", "buen dia", _f(contacts={"juan"}, keywords={"factura"}))


def test_empty_filter_notifies_nothing():
    assert not passes_filter("Ana", "x@s.whatsapp.net", "hola", _f())
