from abc import ABC, abstractmethod


class SecretVault(ABC):
    """A store Ari resolves secrets from without the LLM ever seeing them.
    Names only are ever listed; there is no bulk value dump by design."""

    @abstractmethod
    def get(self, name: str) -> str | None: ...

    @abstractmethod
    def set(self, name: str, value: str) -> None: ...

    @abstractmethod
    def delete(self, name: str) -> None: ...

    @abstractmethod
    def names(self) -> list[str]: ...
