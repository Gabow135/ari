from ari.infrastructure.gateway.outbound_sanitize import strip_binary_blobs


def test_data_uri_is_stripped():
    blob = "data:image/png;base64," + "A" * 300
    out = strip_binary_blobs(f"Acá está la imagen: {blob} fin")
    assert "base64," not in out
    assert "[binario omitido]" in out
    assert out.startswith("Acá está la imagen:") and out.endswith("fin")


def test_long_standalone_base64_run_is_stripped():
    out = strip_binary_blobs("dump:\n" + "Zm9vYmFy" * 100 + "\nlisto")  # 800 chars
    assert "[binario omitido]" in out
    assert "listo" in out


def test_normal_prose_and_short_code_are_untouched():
    text = (
        "Listo, te dejé el dashboard.\n"
        "```bash\nuv run pytest -q\n```\n"
        "El id es 636320 y el hash abc123def456."
    )
    assert strip_binary_blobs(text) == text


def test_short_base64_like_token_is_untouched():
    # A real token/hash well under the 512-char floor must survive.
    token = "A" * 64
    text = f"token: {token}"
    assert strip_binary_blobs(text) == text


def test_empty_is_returned_as_is():
    assert strip_binary_blobs("") == ""
