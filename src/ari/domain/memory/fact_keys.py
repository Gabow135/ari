def normalize_key(key: str) -> str:
    """Canonicalize a fact key: lowercase, trimmed, whitespace runs -> single '_'."""
    return "_".join(key.strip().lower().split())
