import logging
from logging.handlers import RotatingFileHandler

import pytest

from ari.infrastructure.logging_setup import setup_logging


@pytest.fixture
def clean_root():
    """Isolate the root logger: start empty, restore the original setup after."""
    root = logging.getLogger()
    saved, saved_level = list(root.handlers), root.level
    for h in list(root.handlers):
        root.removeHandler(h)
    yield root
    for h in list(root.handlers):
        root.removeHandler(h)
        h.close()
    for h in saved:
        root.addHandler(h)
    root.setLevel(saved_level)


def test_setup_logging_writes_messages_to_file(clean_root, tmp_path):
    log_file = tmp_path / "ari.log"
    setup_logging(str(log_file), level="INFO")
    logging.getLogger("ari.test").info("hola-seguimiento")
    for h in clean_root.handlers:
        h.flush()
    assert log_file.exists()
    assert "hola-seguimiento" in log_file.read_text()


def test_setup_logging_is_idempotent(clean_root, tmp_path):
    log_file = tmp_path / "ari.log"
    setup_logging(str(log_file))
    setup_logging(str(log_file))
    file_handlers = [h for h in clean_root.handlers if isinstance(h, RotatingFileHandler)]
    console_handlers = [h for h in clean_root.handlers if type(h) is logging.StreamHandler]
    assert len(file_handlers) == 1
    assert len(console_handlers) == 1


def test_setup_logging_respects_level(clean_root, tmp_path):
    log_file = tmp_path / "ari.log"
    setup_logging(str(log_file), level="WARNING")
    logging.getLogger("ari.test").info("should-not-appear")
    logging.getLogger("ari.test").warning("should-appear")
    for h in clean_root.handlers:
        h.flush()
    text = log_file.read_text()
    assert "should-appear" in text
    assert "should-not-appear" not in text


def test_setup_logging_without_file_only_configures_console(clean_root):
    setup_logging("", level="INFO")
    file_handlers = [h for h in clean_root.handlers if isinstance(h, RotatingFileHandler)]
    console_handlers = [h for h in clean_root.handlers if type(h) is logging.StreamHandler]
    assert file_handlers == []
    assert len(console_handlers) == 1
