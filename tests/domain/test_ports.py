import importlib
import pkgutil

import ari.domain
from tests.fakes import FakeLLM, FakeMemory, FakeEmbeddings
from ari.domain.agent.message import Message
from datetime import datetime, timezone


async def test_fakes_satisfy_ports():
    llm = FakeLLM(reply="ok")
    emb = FakeEmbeddings(dim=4)
    mem = FakeMemory()
    msg = Message("u1", "user", "hola", datetime.now(timezone.utc))

    assert await llm.complete("sys", [msg]) == "ok"
    assert len(await emb.embed(["hola"])) == 1
    await mem.append_message(msg)
    assert (await mem.recent_messages("u1", 10))[0].content == "hola"


def test_domain_has_no_infra_imports():
    forbidden = {"anthropic", "telegram", "aiosqlite", "sqlite_vec",
                 "fastembed", "pydantic"}
    for mod in pkgutil.walk_packages(ari.domain.__path__, "ari.domain."):
        module = importlib.import_module(mod.name)
        src = getattr(module, "__file__", "") or ""
        with open(src, encoding="utf-8") as fh:
            text = fh.read()
        for name in forbidden:
            assert f"import {name}" not in text, f"{mod.name} imports {name}"
