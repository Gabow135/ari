class SkillAdmin:
    """Owner-facing skill management, callable from the main-process Telegram handlers."""

    def __init__(self, manager, vault_web):
        self._m = manager
        self._vw = vault_web

    def list_text(self) -> str:
        rows = self._m.list()
        if not rows:
            return "No hay skills instalados."
        lines = []
        for s in rows:
            flag = "on" if s.enabled else "off"
            extra = f" — faltan: {', '.join(s.missing_secrets)}" if s.missing_secrets else ""
            lines.append(f"• {s.name} v{s.version} [{flag}/{s.state}]{extra}")
        return "\n".join(lines)

    def enable(self, name: str) -> str:
        if not self._m.set_enabled(name, True):
            return f"No existe el skill '{name}'."
        status = next((s for s in self._m.list() if s.name == name), None)
        if status is not None and status.missing_secrets:
            link = self._vw.new_link()
            return (f"Activé '{name}', pero faltan credenciales: "
                    f"{', '.join(status.missing_secrets)}.\n"
                    f"Cargalas acá (misma red, vence pronto):\n{link}")
        return f"Activé '{name}'."

    def disable(self, name: str) -> str:
        if not self._m.set_enabled(name, False):
            return f"No existe el skill '{name}'."
        return f"Desactivé '{name}'."
