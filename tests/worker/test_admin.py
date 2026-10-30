"""tests/worker/test_admin.py — คำสั่งผู้ดูแล app/worker/admin.py (ไม่ต่อ DB)"""

from __future__ import annotations

from typing import Any

import pytest

from app.pipeline import pdpa
from app.worker import admin

LINE_ID = "U0123456789abcdef0123456789abcdef"


def test_parse_pdpa_delete() -> None:
    args = admin.parse_args(["pdpa-delete", f"  {LINE_ID} "])
    assert args.command == "pdpa-delete"
    assert args.line_user_id == LINE_ID


@pytest.mark.parametrize(
    "argv",
    [[], ["pdpa-delete"], ["pdpa-delete", "   "], ["unknown", LINE_ID], ["pdpa-delete", "a", "b"]],
)
def test_parse_rejects_bad_args(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as e:
        admin.parse_args(argv)
    assert e.value.code == 2


def test_format_result_has_counts_not_raw_id() -> None:
    line = admin.format_result(
        LINE_ID, pdpa.PdpaResult(user_found=True, files_deleted=3, failed_keys=["images/1/m"])
    )
    assert LINE_ID not in line
    assert f"user_ref={pdpa.user_ref(LINE_ID)}" in line
    assert "user_found=yes" in line and "files_deleted=3" in line and "files_failed=1" in line
    assert "images/1/m" not in line


@pytest.mark.parametrize(
    ("result", "code"),
    [
        (pdpa.PdpaResult(user_found=True, files_deleted=1), admin.EXIT_OK),
        (pdpa.PdpaResult(user_found=False), admin.EXIT_OK),
        (pdpa.PdpaResult(user_found=True, failed_keys=["k"]), admin.EXIT_FILES_FAILED),
    ],
)
def test_main_exit_code_and_output(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    result: pdpa.PdpaResult,
    code: int,
) -> None:
    seen: list[str] = []

    async def fake_run(settings: Any, line_user_id: str) -> pdpa.PdpaResult:
        seen.append(line_user_id)
        return result

    monkeypatch.setattr(admin, "run_pdpa_delete", fake_run)
    monkeypatch.setattr(admin, "get_settings", lambda: _S())
    monkeypatch.setattr(admin, "configure_logging", lambda level: None)
    assert admin.main(["pdpa-delete", LINE_ID]) == code
    assert seen == [LINE_ID]
    out = capsys.readouterr().out
    assert LINE_ID not in out
    assert out.startswith("pdpa-delete user_ref=")


class _S:
    log_level = "INFO"


async def test_run_pdpa_delete_closes_resources(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.fakes import make_deps

    deps = make_deps()
    closed: list[Any] = []
    res = object()

    async def build(settings: Any) -> tuple[Any, Any]:
        return deps, res

    async def close(r: Any) -> None:
        closed.append(r)

    monkeypatch.setattr(admin, "build_deps", build)
    monkeypatch.setattr(admin, "close_resources", close)
    out = await admin.run_pdpa_delete(deps.settings, LINE_ID)
    assert out == pdpa.PdpaResult(user_found=False)
    assert closed == [res]


# --- kill-switch / close-link / conversations (fake deps) ---


def _patch_main(monkeypatch: pytest.MonkeyPatch, deps: Any) -> list[Any]:
    """main ใช้ fake deps แทนการต่อ DB จริง; คืน list ของ resource ที่ถูกปิด"""
    closed: list[Any] = []
    res = object()

    async def build(settings: Any) -> tuple[Any, Any]:
        return deps, res

    async def close(r: Any) -> None:
        closed.append(r)

    monkeypatch.setattr(admin, "build_deps", build)
    monkeypatch.setattr(admin, "close_resources", close)
    monkeypatch.setattr(admin, "get_settings", lambda: deps.settings)
    monkeypatch.setattr(admin, "configure_logging", lambda level: None)
    return closed


async def _human(deps: Any, line_user_id: str = LINE_ID) -> Any:
    user = await deps.users.get_or_create(line_user_id)
    conv, _ = await deps.conversations.get_or_open(user.id, deps.settings.state_ttl)
    started = await deps.conversations.start_handoff(conv.id, "rule_escalate")
    assert started is not None
    return started


@pytest.mark.parametrize(
    "argv",
    [
        ["kill-switch"],
        ["kill-switch", "maybe"],
        ["close-link"],
        ["close-link", "abc"],
        ["conversations"],
        ["conversations", "--status", "bot"],
        ["conversations", "--status", "human", "--limit", "0"],
    ],
)
def test_parse_rejects_bad_new_args(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as e:
        admin.parse_args(argv)
    assert e.value.code == 2


async def test_kill_switch_on_off_status() -> None:
    from tests.fakes import make_deps

    deps = make_deps()
    st = await admin.kill_switch(deps, "status")
    assert st == admin.KillSwitchStatus(env_on=False, db_value=None, effective_on=False)
    st = await admin.kill_switch(deps, "on")
    assert st.effective_on and st.db_value == "on"
    st = await admin.kill_switch(deps, "off")
    assert not st.effective_on and st.db_value == "off"
    assert "effective=off env=off db=off" in admin.format_kill_switch(st)


async def test_kill_switch_env_forces_on() -> None:
    from tests.fakes import make_deps, make_settings

    deps = make_deps(settings=make_settings(kill_switch=True))
    st = await admin.kill_switch(deps, "off")
    assert st.env_on and st.effective_on and st.db_value == "off"
    assert "KILL_SWITCH=true" in admin.format_kill_switch(st)


def test_main_kill_switch(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from tests.fakes import make_deps

    deps = make_deps()
    closed = _patch_main(monkeypatch, deps)
    assert admin.main(["kill-switch", "on"]) == admin.EXIT_OK
    assert deps.flags.db.flags["kill_switch"] == "on"
    # structlog ในเทสต์พิมพ์ลง stdout ด้วย -> ดูบรรทัดสุดท้าย
    assert capsys.readouterr().out.strip().splitlines()[-1].startswith("kill-switch effective=on")
    assert len(closed) == 1


async def test_close_link_for_human_is_usable_once() -> None:
    from tests.fakes import make_deps

    deps = make_deps()
    conv = await _human(deps)
    result = await admin.close_link(deps, conv.id)
    assert result.reason is None and result.url is not None
    prefix = f"{deps.settings.public_base_url}/close?token="
    assert result.url.startswith(prefix)
    token = result.url.removeprefix(prefix)
    used = await deps.conversations.consume_close_token(token)
    assert used is not None
    assert used.conversation_id == conv.id and used.handoff_started_at == conv.handoff_started_at
    assert admin.format_close_link(conv.id, result) == result.url


async def test_close_link_refuses() -> None:
    from tests.fakes import make_deps, make_settings

    deps = make_deps()
    assert (await admin.close_link(deps, 999)).reason == admin.CLOSE_NOT_FOUND
    user = await deps.users.get_or_create(LINE_ID)
    bot, _ = await deps.conversations.get_or_open(user.id, deps.settings.state_ttl)
    res = await admin.close_link(deps, bot.id)
    assert res.url is None and res.reason == admin.CLOSE_NOT_HUMAN
    assert "ไม่สร้างลิงก์" in admin.format_close_link(bot.id, res)
    assert not deps.conversations.db.close_tokens

    no_url = make_deps(settings=make_settings(public_base_url=None))
    conv = await _human(no_url)
    assert (await admin.close_link(no_url, conv.id)).reason == admin.CLOSE_NO_BASE_URL
    assert not no_url.conversations.db.close_tokens


def test_main_close_link_exit_codes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import asyncio

    from tests.fakes import make_deps

    deps = make_deps()
    conv = asyncio.run(_human(deps))
    _patch_main(monkeypatch, deps)
    assert admin.main(["close-link", str(conv.id)]) == admin.EXIT_OK
    out = capsys.readouterr().out.strip()
    assert out.startswith("https://bot.example.test/close?token=")
    assert LINE_ID not in out
    assert admin.main(["close-link", "999"]) == admin.EXIT_REFUSED
    assert "ไม่มี conversation นี้" in capsys.readouterr().out


def test_main_conversations_lists_human_only(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import asyncio

    from tests.fakes import make_deps

    deps = make_deps()
    conv = asyncio.run(_human(deps))

    async def _bot() -> None:
        user = await deps.users.get_or_create("U-other")
        await deps.conversations.get_or_open(user.id, deps.settings.state_ttl)

    asyncio.run(_bot())
    _patch_main(monkeypatch, deps)
    assert admin.main(["conversations", "--status", "human"]) == admin.EXIT_OK
    lines = capsys.readouterr().out.strip().splitlines()
    assert lines[0].startswith(f"conversation_id={conv.id} user_id={conv.user_id} status=human ")
    assert "handoff_reason=rule_escalate" in lines[0]
    assert lines[-1] == "conversations status=human count=1"
    assert all(LINE_ID not in line for line in lines)
