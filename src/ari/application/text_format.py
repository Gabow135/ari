def truncate(text: str, limit: int = 120) -> str:
    """Shorten text for a chat message, ending in an ellipsis when cut."""
    return text if len(text) <= limit else text[:limit - 1] + "…"
