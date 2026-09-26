import pytest

from ari.domain.ports.llm_port import LLMTimeoutError
from ari.domain.tools.toolset import WEB_TOOLS, Toolset, ToolsView


def test_toolset_is_immutable_value():
    t = Toolset(WEB_TOOLS, WEB_TOOLS)
    assert t.mcp_config_path is None
    with pytest.raises(AttributeError):
        t.mcp_config_path = "x"  # frozen


def test_tools_view_fields():
    v = ToolsView("## Tus herramientas", has_web=True, has_mcp=False)
    assert (v.has_web, v.has_mcp) == (True, False)


def test_timeout_error_is_a_runtime_error():
    assert issubclass(LLMTimeoutError, RuntimeError)
