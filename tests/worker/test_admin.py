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
