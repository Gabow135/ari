import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WhatsAppFilter:
    contacts: frozenset[str]
    keywords: frozenset[str]


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.casefold().strip()


def passes_filter(contact_name: str, wa_chat_id: str, text: str, f: WhatsAppFilter) -> bool:
    name_n = _norm(contact_name)
    number_n = _norm(wa_chat_id.split("@", 1)[0])
    for c in f.contacts:
        cn = _norm(c)
        if cn and (cn == name_n or cn in number_n):
            return True
    text_n = _norm(text)
    return any((kn := _norm(k)) and kn in text_n for k in f.keywords)
