import json
import logging

import pytest

from app.logging import _HANDLER_MARK, bind_ids, clear_ids, configure_logging


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    clear_ids()
    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, _HANDLER_MARK, False):
            root.removeHandler(h)


def _lines(capsys):
    return [json.loads(x) for x in capsys.readouterr().out.splitlines() if x.strip()]


def test_stdlib_record_rendered_as_json(capsys):
    configure_logging("INFO")
    bind_ids(raw_event_id=7)
    logging.getLogger("procrastinate.worker").info("x %s", "y")
    (line,) = _lines(capsys)
    assert line["event"] == "x y"
    assert line["level"] == "info"
    assert "timestamp" in line
    assert line["raw_event_id"] == 7


def test_stdlib_message_is_redacted(capsys):
    configure_logging("INFO")
    logging.getLogger("uvicorn").info("call me 0812345678")
    out = capsys.readouterr().out
    assert "0812345678" not in out


def test_configure_twice_single_handler():
    configure_logging("INFO")
    configure_logging("DEBUG")
    root = logging.getLogger()
    assert sum(1 for h in root.handlers if getattr(h, _HANDLER_MARK, False)) == 1
    assert root.level == logging.DEBUG
    assert logging.getLogger("httpx").level == logging.WARNING
