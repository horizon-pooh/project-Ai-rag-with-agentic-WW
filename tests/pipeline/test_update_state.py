"""tests/pipeline/test_update_state.py — next_state / state_expiry (app/pipeline/steps/update_state.py)"""

from __future__ import annotations

from datetime import timedelta

from app.pipeline.schemas import AiKind, AiResult, Counters, Product
from app.pipeline.steps.update_state import next_state, state_expiry
from tests.fakes import DEFAULT_NOW, make_settings


def _p(sku: str) -> Product:
    return Product(sku=sku, name=f"สินค้า {sku}", url=f"https://shop.example/{sku}")


def test_state_out_replaces_prev_state() -> None:
    prev = {"category": "ตู้เย็น"}
    r = AiResult(kind=AiKind.ANSWER, intro="x", state_out={"category": "ทีวี"})
    state, _ = next_state(prev, r, Counters())
    assert state == {"category": "ทีวี"}
    assert prev == {"category": "ตู้เย็น"}


def test_empty_state_out_clears_state() -> None:
    state, _ = next_state(
        {"a": 1}, AiResult(kind=AiKind.ANSWER, intro="x", state_out={}), Counters()
    )
    assert state == {}


def test_missing_state_out_keeps_prev_state() -> None:
    prev = {"category": "ตู้เย็น", "color": "เขียว"}
    state, _ = next_state(prev, AiResult(kind=AiKind.ANSWER, intro="x"), Counters())
    assert state == prev
    state, _ = next_state(None, AiResult(kind=AiKind.ANSWER, intro="x"), Counters())
    assert state is None


def test_products_replace_last_products() -> None:
    old = Counters(last_products=[_p("OLD")], clarify_streak=1)
    r = AiResult(kind=AiKind.PRODUCTS, products=[_p("A"), _p("B")])
    _, c = next_state(None, r, old)
    assert [p.sku for p in c.last_products] == ["A", "B"]
    assert c.clarify_streak == 1
    assert [p.sku for p in old.last_products] == ["OLD"]  # ของเดิมไม่ถูกแก้


def test_no_products_keeps_last_products() -> None:
    old = Counters(last_products=[_p("OLD")])
    _, c = next_state(None, AiResult(kind=AiKind.ANSWER, intro="x"), old)
    assert c == old


def test_state_expiry_uses_state_ttl() -> None:
    assert state_expiry(DEFAULT_NOW, make_settings()) == DEFAULT_NOW + timedelta(hours=2)
    assert state_expiry(DEFAULT_NOW, make_settings(state_ttl_hours=0.5)) == DEFAULT_NOW + timedelta(
        minutes=30
    )
