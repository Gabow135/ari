import pytest

from ari.infrastructure.email.account_form import PROVIDERS, render_enroll_html


def test_presets_cover_the_three_known_providers():
    assert {"gmail", "outlook", "corp", "custom"} <= set(PROVIDERS)
    assert PROVIDERS["gmail"]["imap_host"] == "imap.gmail.com"


def test_html_embeds_key_form_and_sodium():
    html = render_enroll_html("PUBKEYB64==")
    assert "PUBKEYB64==" in html
    assert "crypto_box_seal" in html          # inlined sodium is present
    assert "ari-mail:v1:" in html             # output prefix
    assert 'name="password"' in html
    assert "base64_variants.ORIGINAL" in html  # pinned variant (see Review Focus)


def test_rejects_non_base64_public_key():
    with pytest.raises(ValueError):
        render_enroll_html('"; alert(1); //')
