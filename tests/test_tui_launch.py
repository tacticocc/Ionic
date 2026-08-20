"""TTY boundary tests for the interactive Ionic entry point."""

from __future__ import annotations

from io import StringIO

import pytest

from ionic import cli


class FakeStream(StringIO):
    def __init__(self, *, is_tty: bool) -> None:
        super().__init__()
        self._is_tty = is_tty

    def isatty(self) -> bool:
        return self._is_tty


@pytest.mark.parametrize(
    ("argv", "stdin_tty", "stdout_tty", "environment", "expected"),
    [
        ([], True, True, {}, True),
        (["status", "--json"], True, True, {}, False),
        ([], False, True, {}, False),
        ([], True, False, {}, False),
        ([], True, True, {"CI": "1"}, False),
        ([], True, True, {"IONIC_NO_TUI": "1"}, False),
    ],
)
def test_tui_launch_requires_bare_interactive_terminal(
    argv, stdin_tty, stdout_tty, environment, expected
):
    assert cli._should_launch_tui(
        argv=argv,
        input_stream=FakeStream(is_tty=stdin_tty),
        output_stream=FakeStream(is_tty=stdout_tty),
        environ=environment,
    ) is expected


@pytest.mark.parametrize(
    ("error", "expected_code"),
    [(KeyboardInterrupt(), 130), (EOFError(), 0)],
)
def test_main_maps_terminal_exit_signals(monkeypatch, error, expected_code):
    import ionic.tui

    monkeypatch.setattr(cli, "_should_launch_tui", lambda: True)

    def stop():
        raise error

    monkeypatch.setattr(ionic.tui, "run_tui", stop)
    with pytest.raises(SystemExit) as raised:
        cli.main()
    assert raised.value.code == expected_code


def test_main_returns_tui_exit_code(monkeypatch):
    import ionic.tui

    monkeypatch.setattr(cli, "_should_launch_tui", lambda: True)
    monkeypatch.setattr(ionic.tui, "run_tui", lambda: 7)
    with pytest.raises(SystemExit) as raised:
        cli.main()
    assert raised.value.code == 7
