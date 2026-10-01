"""Skill-authoring contract injection for the Claude Code coder.

When the coding instruction is about creating or editing a skill, the
authoritative contract is appended so the generated skill always matches
what SkillManager expects to load.
"""

SKILL_CONTRACT = """\
--- SKILL AUTHORING CONTRACT (follow exactly) ---

Directory layout:
  skills/<name>/skill.json   — manifest
  skills/<name>/skill.py     — implementation

skill.json REQUIRED keys:
  "name"       — unique skill identifier (string)
  "entrypoint" — filename of the skill module, e.g. "skill.py"
  "factory"    — callable name exported by the module, e.g. "build_skill"
  "enabled"    — set to true to activate the skill

skill.json OPTIONAL keys:
  "version", "description", "owner_only", "priority",
  "required_secrets", "optional_secrets",
  "hooks" (e.g. ["inbound_transform"]),
  "config" (arbitrary key/value dict forwarded to the factory as ctx.config)

skill.py REQUIRED:
  - Export a top-level factory function: def build_skill(config) -> <SkillInstance>
  - An inbound skill must implement: async def on_inbound(self, raw, ctx) -> str | None
    Return a string to short-circuit further processing, or None to pass through.

Runtime context (ctx):
  - ctx.secret(NAME)          — read a required secret (must be listed in required_secrets)
  - ctx.optional_secret(NAME) — read an optional secret (must be in optional_secrets); returns None if absent
  - ctx.config                — dict from the manifest "config" block

Heavy / optional imports (torch, pyannote, etc.):
  Wrap in try/except at module level so the module always imports cleanly.
  Return None from on_inbound when a dependency or secret is unavailable.

Test requirement:
  ADD A TEST under tests/ that uses fakes (no network, no model download).
  The test must pass under: uv run pytest -m "not slow"

References: skills/documents/ and skills/voice_id/ as working examples.
--- END SKILL AUTHORING CONTRACT ---\
"""


def is_skill_task(text: str) -> bool:
    """Return True when the coding instruction is about creating or editing a skill."""
    t = (text or "").lower()
    return "skill" in t or "habilidad" in t


def augment_for_skill(text: str) -> str:
    """Append the skill-authoring contract when the task is skill-related.

    Returns the original text unchanged when the task is not skill-related.
    """
    if not is_skill_task(text):
        return text
    return f"{text}\n\n{SKILL_CONTRACT}"
