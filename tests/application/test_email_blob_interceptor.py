from ari.application.email.intercept import EmailBlobInterceptor


class FakeConnect:
    def __init__(self, result): self._result = result
    async def __call__(self, user_id, blob):
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


async def test_success_reply_names_the_label():
    interceptor = EmailBlobInterceptor(FakeConnect("trabajo"))
    reply = await interceptor.handle("7", "ari-mail:v1:xxx")
    assert "trabajo" in reply and "conectada" in reply.lower()


async def test_failure_returns_error_not_raises():
    interceptor = EmailBlobInterceptor(FakeConnect(ValueError("blob ilegible")))
    reply = await interceptor.handle("7", "ari-mail:v1:bad")
    assert "blob ilegible" in reply


async def test_runtime_error_returns_generic_message_not_raises():
    """A RuntimeError (e.g. missing ARI_VAULT_KEY) must never escape handle()."""
    interceptor = EmailBlobInterceptor(
        FakeConnect(RuntimeError("ARI_VAULT_KEY no está configurada"))
    )
    reply = await interceptor.handle("7", "ari-mail:v1:xxx")
    assert reply  # non-empty
    assert "ARI_VAULT_KEY" not in reply  # no internal detail leaked
    assert "correo" in reply.lower() or "conectar" in reply.lower()
