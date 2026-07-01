import asyncio

import pytest
from lyra.tui import LyraTuiApp, TuiConfig
from lyra.tui.__main__ import build_parser, config_from_args, main
from textual.widgets import Footer, Header, TabbedContent


def test_parser_defaults_to_local_http(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LYRA_ADMIN_API_KEY", raising=False)

    args = build_parser().parse_args([])
    config = config_from_args(args)

    assert config == TuiConfig(
        host="localhost:5219",
        secure=False,
        admin_api_key=None,
        timeout=30.0,
        refresh_interval=5.0,
    )


def test_parser_reads_admin_key_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LYRA_ADMIN_API_KEY", "secret")

    args = build_parser().parse_args([])
    config = config_from_args(args)

    assert config.admin_api_key == "secret"
    assert config.has_admin_key


def test_parser_prefers_explicit_admin_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LYRA_ADMIN_API_KEY", "from-env")

    args = build_parser().parse_args(["--admin-api-key", "from-cli"])
    config = config_from_args(args)

    assert config.admin_api_key == "from-cli"


def test_help_exits_before_starting_app(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert "lyra-tui" in output
    assert "--host" in output
    assert "--admin-api-key" in output
    assert "--refresh-interval" in output
    assert "--secure" in output
    assert "--no-secure" in output
    assert "uv run lyra-tui --host localhost:5219 --no-secure" in output
    assert "LYRA_ADMIN_API_KEY" in output


def test_parser_rejects_url_scheme_in_host() -> None:
    parser = build_parser()

    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["--host", "http://localhost:5219"])

    assert exc_info.value.code == 2


def test_parser_rejects_non_positive_refresh_interval() -> None:
    parser = build_parser()

    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["--refresh-interval", "0"])

    assert exc_info.value.code == 2


def test_parser_rejects_non_finite_timeout() -> None:
    parser = build_parser()

    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["--timeout", "nan"])

    assert exc_info.value.code == 2


def test_app_composes_tabbed_shell() -> None:
    async def run() -> None:
        app = LyraTuiApp(TuiConfig(admin_api_key="secret"), poll_on_mount=False)
        async with app.run_test():
            assert app.query_one(Header) is not None
            assert app.query_one(TabbedContent) is not None
            assert app.query_one(Footer) is not None

    asyncio.run(run())


def test_q_quits_app() -> None:
    async def run() -> None:
        app = LyraTuiApp(TuiConfig(admin_api_key="secret"), poll_on_mount=False)
        async with app.run_test() as pilot:
            await pilot.press("q")
            await pilot.pause()
            assert app.return_code == 0

    asyncio.run(run())
