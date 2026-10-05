from ari.infrastructure.email.html_text import html_to_text


def test_strips_tags_and_decodes_entities():
    out = html_to_text("<p>Total&nbsp;USD&amp;: 1.234</p>")
    assert "Total" in out and "USD&:" in out and "1.234" in out
    assert "<p>" not in out


def test_drops_script_and_style():
    out = html_to_text("<style>.a{}</style><script>x()</script><p>hola</p>")
    assert "hola" in out
    assert "x()" not in out and ".a{}" not in out


def test_block_tags_become_line_breaks():
    out = html_to_text("<div>uno</div><div>dos</div>")
    assert "uno" in out and "dos" in out
    assert "\n" in out.strip()


def test_empty_input_is_empty():
    assert html_to_text("") == ""
    assert html_to_text(None) == ""
