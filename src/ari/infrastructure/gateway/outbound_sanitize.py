"""Strip binary blobs (base64 / data: URIs) from text sent to Telegram, so raw
data from processing documents or images never floods the chat."""
import re

_DATA_URI = re.compile(r"data:[\w.+-]+/[\w.+-]+;base64,[A-Za-z0-9+/=]+", re.IGNORECASE)
_LONG_B64 = re.compile(r"[A-Za-z0-9+/]{512,}={0,2}")
_PLACEHOLDER = "[binario omitido]"


def strip_binary_blobs(text: str) -> str:
    """Replace data: URIs and long standalone base64 runs with a short marker.
    The 512-char floor keeps normal prose, IDs, hashes, and short code intact."""
    if not text:
        return text
    text = _DATA_URI.sub(_PLACEHOLDER, text)
    text = _LONG_B64.sub(_PLACEHOLDER, text)
    return text
