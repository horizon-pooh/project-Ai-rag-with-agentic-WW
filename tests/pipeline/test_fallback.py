"""tests/pipeline/test_fallback.py — fallback (app/pipeline/steps/fallback.py)"""

from __future__ import annotations

import pytest

from app.pipeline import replies
from app.pipeline.schemas import AiBadResponse, AiError, AiUnavailable
from app.pipeline.steps.fallback import fallback

ERRORS = [
    AiUnavailable("timeout", error_class="timeout", attempts=2),
    AiBadResponse("bad json", error_class="parse_error"),
]


@pytest.mark.parametrize("error", ERRORS)
def test_risky_escalates_without_outbound(error: AiError) -> None:
    d = fallback(error, risky=True)
    assert d.escalate is True
    assert d.outbound is None
    assert d.error_class == error.error_class


@pytest.mark.parametrize("error", ERRORS)
def test_not_risky_apologizes_with_staff_button(error: AiError) -> None:
    d = fallback(error, risky=False)
    assert d.escalate is False
    assert d.error_class == error.error_class
    assert d.outbound is not None
    assert d.outbound.messages == [replies.ai_down_message()]
    items = d.outbound.messages[0]["quickReply"]["items"]
    assert items[0]["action"]["data"] == "action=talk_to_staff"
