def missing_inbound_secrets(statuses) -> list[str]:
    """Secret names missing for inbound-capable skills stuck in needs_secrets (deduped, sorted).

    A voice note that produced no text while such a skill exists means Ari needs that
    credential — the caller offers the vault link instead of a generic failure."""
    return sorted({
        name
        for s in statuses
        if s.state == "needs_secrets" and "inbound_transform" in s.hooks
        for name in s.missing_secrets
    })
