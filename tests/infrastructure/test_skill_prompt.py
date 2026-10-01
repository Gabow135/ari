"""Tests for skill_prompt.py — is_skill_task and augment_for_skill."""
from ari.infrastructure.coder.skill_prompt import augment_for_skill, is_skill_task

# ---------------------------------------------------------------------------
# is_skill_task
# ---------------------------------------------------------------------------


def test_is_skill_task_true_for_english_skill():
    assert is_skill_task("create a skill that fetches weather") is True


def test_is_skill_task_true_for_spanish_habilidad():
    assert is_skill_task("agregá una habilidad para el calendario") is True


def test_is_skill_task_false_for_unrelated():
    assert is_skill_task("fix the vault bug in the config") is False


def test_is_skill_task_false_for_empty():
    assert is_skill_task("") is False


def test_is_skill_task_none_safe():
    # None should not raise; coerces to empty string
    assert is_skill_task(None) is False  # type: ignore[arg-type]


def test_is_skill_task_case_insensitive():
    assert is_skill_task("Build a SKILL for reminders") is True


# ---------------------------------------------------------------------------
# augment_for_skill
# ---------------------------------------------------------------------------


def test_augment_appends_contract_for_skill_task():
    result = augment_for_skill("create a skill that greets the user")
    assert "skill.json" in result
    assert result.startswith("create a skill")


def test_augment_returns_unchanged_for_non_skill():
    text = "refactor the webhook handler"
    result = augment_for_skill(text)
    assert result == text
    assert "skill.json" not in result


def test_augment_contract_contains_required_keys_hint():
    result = augment_for_skill("write a skill for reminders")
    assert "name" in result
    assert "entrypoint" in result
    assert "factory" in result


def test_augment_contract_mentions_build_skill():
    result = augment_for_skill("edit the documents skill")
    assert "build_skill" in result


def test_augment_contract_mentions_test():
    result = augment_for_skill("add a skill")
    assert "test" in result.lower()
