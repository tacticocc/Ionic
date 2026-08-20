from __future__ import annotations

from io import StringIO
from pathlib import Path
from types import SimpleNamespace

from ionic.tui import (
    MAX_COMMAND_CHARS,
    MAX_HISTORY_ENTRIES,
    MAX_TRANSCRIPT_CHARS,
    CommandDispatcher,
    DispatchResult,
    ShellState,
    banner,
    command_candidates,
    command_specs,
    operation_boundary_text,
    run_tui,
    welcome_screen,
    _local_path_candidates,
    _plain_terminal_output,
)


def test_banner_is_ascii_and_handles_narrow_terminals():
    assert banner(20).strip() == "(###)==( )  IONIC"
    assert ".------." in banner(80)
    assert "___" in banner(80)
    assert banner(80).isascii()


def test_welcome_screen_is_responsive_ascii_and_names_next_actions():
    full = welcome_screen(80)
    narrow = welcome_screen(32)
    minimum = welcome_screen(20)
    compact_width = welcome_screen(42)
    compact_mid = welcome_screen(64)
    compact_height = welcome_screen(80, 18)

    assert "IONIC ESSENTIAL" in full
    assert "/workspace scan" in full
    assert all(len(line) <= 80 for line in full.splitlines())
    assert "(###)==( )  IONIC" in narrow
    assert all(len(line) <= 32 for line in narrow.splitlines())
    assert all(len(line) <= 20 for line in minimum.splitlines())
    assert all(len(line) <= 42 for line in compact_width.splitlines())
    assert all(len(line) <= 64 for line in compact_mid.splitlines())
    assert all(len(line) <= 80 for line in compact_height.splitlines())
    assert "(###)==( )  IONIC" in compact_width
    assert "local contract control" in compact_mid
    assert "(###)==( )  IONIC" in compact_height
    assert full.isascii()
    assert narrow.isascii()


def test_wide_welcome_matches_launch_card_proportions_and_upper_third_rhythm():
    rendered = welcome_screen(161, 57)
    lines = rendered.splitlines()
    visible = [line for line in lines if line.strip()]
    border = visible[0]

    assert 118 <= len(border.strip()) <= 124
    assert lines.index(border) >= 7
    assert len(visible) <= 13
    assert ".------." in rendered
    assert "IONIC ESSENTIAL  0.7.0" in rendered
    assert "___   ___" not in rendered
    assert all(len(line) <= 161 for line in lines)


def test_fullscreen_welcome_uses_tui_borders_and_canonical_terminal_brand():
    from wcwidth import wcswidth

    rendered = welcome_screen(161, 57, box_drawing=True)
    visible = [line for line in rendered.splitlines() if line.strip()]

    assert "╭" in rendered and "╮" in rendered
    assert "╰" in rendered and "╯" in rendered
    assert "│" in rendered and "─" in rendered
    assert "•••••••••" in rendered
    assert "●●●●●●●●●" in rendered
    assert "○○○○○○○○○" in rendered
    assert "●●●●○○" in rendered
    assert "═" not in rendered
    assert "·········" in rendered
    assert "######" not in rendered
    assert rendered.encode("cp950")
    assert "IONIC ESSENTIAL" not in visible[0]
    assert "IONIC ESSENTIAL  0.7.0" in rendered
    assert all(wcswidth(line) <= 161 for line in rendered.splitlines())

    compact = welcome_screen(42, 24, box_drawing=True)
    assert "●═○  IONIC ESSENTIAL  0.7.0" in compact
    assert compact.count("IONIC ESSENTIAL") == 1

    narrow = welcome_screen(20, 24, box_drawing=True)
    assert "●═○  IONIC" in narrow
    assert "ESSENTIAL 0.7.0" in narrow


def test_fullscreen_workspace_caption_respects_cjk_and_emoji_cell_width(monkeypatch):
    from pathlib import Path

    from wcwidth import wcswidth

    unicode_path = Path("C:/使用者/玩家/專案/🧪-Ionic公開版")
    monkeypatch.setattr(
        "ionic.tui.Path.cwd", classmethod(lambda cls: unicode_path)
    )

    for width in (42, 64, 80, 133, 134, 161):
        rendered = welcome_screen(width, 24, box_drawing=True)
        assert all(wcswidth(line) <= width for line in rendered.splitlines())

    assert "•••••••••" not in welcome_screen(133, 24, box_drawing=True)
    assert "●═○  IONIC ESSENTIAL" in welcome_screen(133, 24, box_drawing=True)
    assert "•••••••••" in welcome_screen(134, 24, box_drawing=True)


def test_status_rail_is_truthful_about_opt_in_remote_semantic_review():
    status = operation_boundary_text().lower()
    assert "registry local" in status
    assert "semantic review opt-in" in status
    assert "no ionic telemetry" in status
    assert "local only" not in status


def test_dispatcher_uses_allowlisted_cli_argv_and_preserves_windows_paths():
    calls = []

    def invoke(argv, environ):
        calls.append((argv, environ))
        return 0, "registered"

    dispatcher = CommandDispatcher(invoker=invoke, environ={"IONIC_HOME": "test-home"})
    result = dispatcher.dispatch('/register "C:\\work\\agent files"')

    assert result == DispatchResult("command", "registered", 0, ("register", "C:\\work\\agent files"))
    assert calls == [(["register", "C:\\work\\agent files"], {"IONIC_HOME": "test-home"})]


def test_dispatcher_supports_aliases_help_and_special_session_commands():
    calls = []
    dispatcher = CommandDispatcher(invoker=lambda argv, env: calls.append(argv) or (0, "ok"))

    assert dispatcher.dispatch("/ls").argv == ("list",)
    assert calls == [["list"]]
    assert "Ionic commands" in dispatcher.dispatch("/help").output
    assert dispatcher.dispatch("/clear").kind == "clear"
    assert dispatcher.dispatch("/q").kind == "exit"


def test_dispatcher_refuses_shell_and_unknown_or_invalid_nested_commands():
    dispatcher = CommandDispatcher(invoker=lambda argv, env: (0, "should not run"))

    assert dispatcher.dispatch("dir").exit_code == 2
    assert dispatcher.dispatch("/powershell -Command nope").exit_code == 2
    result = dispatcher.dispatch("/workspace delete")
    assert result.exit_code == 2
    assert "expects one of" in result.output
    assert dispatcher.dispatch("/serve").exit_code == 2
    assert dispatcher.dispatch("/" + "x" * MAX_COMMAND_CHARS).exit_code == 2


def test_dispatcher_help_forwards_to_existing_cli_help():
    calls = []
    dispatcher = CommandDispatcher(invoker=lambda argv, env: calls.append(argv) or (0, "usage"))

    assert dispatcher.dispatch("/help check").argv == ("check", "--help")
    assert dispatcher.dispatch("/help workspace").argv == ("workspace", "--help")
    assert calls == [["check", "--help"], ["workspace", "--help"]]


def test_default_dispatcher_preserves_actionable_cli_stderr():
    result = CommandDispatcher().dispatch("/show")

    assert result.exit_code == 2
    assert "Missing argument" in result.output


def test_cli_output_strips_terminal_styling_before_transcript_rendering():
    assert _plain_terminal_output("\x1b[1;36m0.7\x1b[0m") == "0.7"


def test_completion_is_deterministic_and_non_mutating():
    assert command_candidates("/wo") == ["/workspace check", "/workspace scan", "/workspace sync"]
    assert "/status" in command_candidates("/")
    assert "/version" in command_candidates("/v")
    assert command_candidates("/missing") == []
    assert command_specs("/st")[0].description == "Inspect registry health and drift"
    assert command_specs("/d")[0].badge == "new"
    assert command_specs("/repo a")[0].completion == "/repo add "


def test_dashboard_reuses_status_and_reports_session_context(tmp_path):
    calls = []
    repository = tmp_path / "web"
    repository.mkdir()
    dispatcher = CommandDispatcher(
        invoker=lambda argv, env: calls.append(argv) or (0, "registry clean"),
        base_path=tmp_path,
    )

    assert dispatcher.dispatch(f'/repo add web "{repository}"').exit_code == 0
    result = dispatcher.dispatch("/dashboard")

    assert calls == [["status"]]
    assert result.argv == ("status",)
    assert "Ionic dashboard" in result.output
    assert "registry clean" in result.output
    assert "web =" in result.output
    assert "not persisted" in result.output

    dispatcher.dispatch("/help dashboard")
    assert calls[-1] == ["status", "--help"]


def test_repository_session_commands_never_mutate_until_workspace_operation(tmp_path):
    calls = []
    alpha = tmp_path / "alpha repo"
    beta = tmp_path / "beta"
    alpha.mkdir()
    beta.mkdir()
    dispatcher = CommandDispatcher(
        invoker=lambda argv, env: calls.append(argv) or (0, "ok"),
        base_path=tmp_path,
    )

    assert "session only" in dispatcher.dispatch('/repo add alpha "alpha repo"').output
    assert "session only" in dispatcher.dispatch("/repo add beta beta").output
    assert calls == []
    assert dispatcher.selected_repository_id == "alpha"
    assert "* alpha" in dispatcher.dispatch("/repo list").output
    assert dispatcher.dispatch("/repo select beta").exit_code == 0
    assert dispatcher.selected_repository_id == "beta"

    scanned = dispatcher.dispatch("/workspace scan")
    assert scanned.argv == (
        "workspace",
        "scan",
        "--repo",
        f"alpha={alpha.resolve()}",
        "--repo",
        f"beta={beta.resolve()}",
    )
    assert calls[-1] == list(scanned.argv)

    explicit = dispatcher.dispatch('/workspace scan --repo "manual=C:\\work path"')
    assert explicit.argv == ("workspace", "scan", "--repo", "manual=C:\\work path")
    assert dispatcher.dispatch("/repo remove alpha").exit_code == 0
    assert len(dispatcher.repositories) == 1


def test_repository_session_validation_is_fail_closed(tmp_path):
    root = tmp_path / "root"
    child = root / "child"
    file_path = tmp_path / "file.txt"
    child.mkdir(parents=True)
    file_path.write_text("x", encoding="utf-8")
    dispatcher = CommandDispatcher(invoker=lambda argv, env: (0, "unused"), base_path=tmp_path)

    assert dispatcher.dispatch("/repo add Upper root").exit_code == 2
    assert dispatcher.dispatch("/repo add missing nope").exit_code == 2
    assert dispatcher.dispatch("/repo add file file.txt").exit_code == 2
    assert dispatcher.dispatch("/repo add root root").exit_code == 0
    assert "overlaps" in dispatcher.dispatch("/repo add child root/child").output
    assert dispatcher.dispatch("/repo select absent").exit_code == 2
    assert dispatcher.dispatch("/repo remove absent").exit_code == 2


def test_local_path_candidates_are_bounded_non_recursive_and_quote_spaces(tmp_path):
    (tmp_path / "alpha repo").mkdir()
    (tmp_path / "alpha-file.txt").write_text("x", encoding="utf-8")
    nested = tmp_path / "alpha repo" / "nested"
    nested.mkdir()

    candidates = _local_path_candidates(
        "alp", base_path=tmp_path, directories_only=True
    )

    assert candidates == [('"alpha repo" ', "alpha repo\\")]
    assert all("nested" not in insertion for insertion, _ in candidates)
    assert _local_path_candidates(
        "\\\\server\\share", base_path=tmp_path, directories_only=True
    ) == []


def test_shell_state_renders_scrollable_transcript_and_clear():
    state = ShellState()
    state.append("/status", DispatchResult("command", "clean"))
    assert "> /status\n[ok]\nclean" in state.text
    state.append("/show missing", DispatchResult("error", "not found", 2))
    assert "[error 2]\nnot found" in state.text
    state.append("/clear", DispatchResult("clear"))
    assert state.text == ""


def test_shell_state_bounds_long_running_session_output():
    state = ShellState()
    for index in range(650):
        state.append(f"/status {index}", DispatchResult("command", "x" * 1_000))

    assert len(state.text) <= MAX_TRANSCRIPT_CHARS + 5_000
    assert len(state.transcript) <= 100
    assert len(state.history) == MAX_HISTORY_ENTRIES


def test_plain_fallback_is_injectable_and_exits_on_quit():
    output = StringIO()
    dispatcher = CommandDispatcher(invoker=lambda argv, env: (0, "clean"))
    code = run_tui(
        input_stream=StringIO("/status\n/quit\n"),
        output=output,
        is_tty=False,
        environ={"NO_COLOR": "1"},
        dispatcher=dispatcher,
        width=20,
    )

    assert code == 0
    rendered = output.getvalue()
    assert "(###)==( )  IONIC" in rendered
    assert "clean" in rendered
    assert "\x1b" not in rendered


def test_plain_fallback_treats_eof_as_ctrl_d():
    output = StringIO()
    assert run_tui(
        input_stream=StringIO(), output=output, is_tty=False, dispatcher=CommandDispatcher()
    ) == 0


def test_plain_fallback_reports_a_silent_nonzero_exit():
    output = StringIO()
    dispatcher = CommandDispatcher(invoker=lambda argv, env: (7, ""))
    code = run_tui(
        input_stream=StringIO("/status\n/quit\n"),
        output=output,
        is_tty=False,
        dispatcher=dispatcher,
        width=40,
    )

    assert code == 0
    assert "Command exited with status 7." in output.getvalue()


def test_fullscreen_layout_keeps_context_scrollback_composer_and_status_docked(monkeypatch):
    import prompt_toolkit.application

    captured = {}

    class FakeApplication:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def run(self):
            return 0

    monkeypatch.setattr(prompt_toolkit.application, "Application", FakeApplication)
    code = run_tui(
        input_stream=StringIO(),
        output=StringIO(),
        is_tty=True,
        environ={"TERM": "xterm-256color"},
        dispatcher=CommandDispatcher(),
        width=80,
    )

    assert code == 0
    assert captured["full_screen"] is True
    assert captured["mouse_support"] is True
    assert captured["style"] is not None
    assert len(captured["layout"].container.children) == 5
    assert captured["before_render"] is not None
    style_rules = " ".join(rule for _, rule in captured["style"].style_rules)
    assert "bg:#090909" in style_rules
    assert "bg:#0e0f10" in style_rules
    assert "#26dbff" in style_rules
    assert "#2dcbea" not in style_rules
    assert "#101214" not in style_rules
    welcome_control = next(
        control
        for control in captured["layout"].find_all_controls()
        if hasattr(control, "buffer") and "IONIC ESSENTIAL" in control.buffer.text
    )
    welcome_buffer = welcome_control.buffer
    captured["before_render"](
        SimpleNamespace(
            output=SimpleNamespace(
                get_size=lambda: SimpleNamespace(columns=161, rows=57)
            )
        )
    )
    assert "IONIC ESSENTIAL  0.7.0" in welcome_buffer.text
    assert max(map(len, welcome_buffer.text.splitlines())) <= 161
    assert welcome_buffer.text.splitlines().index(
        next(line for line in welcome_buffer.text.splitlines() if line.strip())
    ) >= 7

    document = welcome_buffer.document
    exact_line = next(
        index
        for index, line in enumerate(document.lines)
        if "Exact operations. No shell." in line
    )
    exact_fragments = welcome_control.lexer.lex_document(document)(exact_line)
    exact_visual = "".join(text for _, text in exact_fragments)
    assert "○" not in exact_visual
    assert any(
        style == "class:welcome.logo.ring" and "●" in text
        for style, text in exact_fragments
    )
    assert any(
        style == "class:welcome.description"
        and "Exact operations. No shell." in text
        for style, text in exact_fragments
    )
    assert sum(
        text.count("│")
        for style, text in exact_fragments
        if style == "class:welcome.border"
    ) == 2

    halo_line = next(
        index for index, line in enumerate(document.lines) if "·········" in line
    )
    halo_fragments = welcome_control.lexer.lex_document(document)(halo_line)
    halo_visual = "".join(text for _, text in halo_fragments)
    assert "·" not in halo_visual
    assert any(style == "class:welcome.logo.halo.cyan" for style, _ in halo_fragments)
    assert any(style == "class:welcome.logo.halo.white" for style, _ in halo_fragments)
    focusable = [
        control
        for control in captured["layout"].find_all_controls()
        if control.is_focusable()
    ]
    assert len(focusable) >= 2
    binding_names = {
        str(key).lower()
        for binding in captured["key_bindings"].bindings
        for key in binding.keys
    }
    assert any("tab" in name for name in binding_names)
    assert any("pageup" in name for name in binding_names)
    assert any("pagedown" in name for name in binding_names)
    assert any(name == "keys.up" for name in binding_names)
    assert any(name == "keys.down" for name in binding_names)

    from prompt_toolkit.completion import CompleteEvent
    from prompt_toolkit.document import Document

    composer_control = captured["layout"].current_control
    completions = list(
        composer_control.buffer.completer.get_completions(
            Document("/", cursor_position=1),
            CompleteEvent(completion_requested=True),
        )
    )
    assert completions[0].display_text == "/dashboard"
    assert completions[0].text == "/dashboard "
    assert "[new]" in completions[0].display_meta_text
    assert "registry health" in completions[0].display_meta_text

    from prompt_toolkit.buffer import CompletionState

    composer_control.buffer.complete_state = CompletionState(
        Document("/", cursor_position=1), completions, 0
    )
    palette_control = captured["layout"].container.children[1].content.children[1].content
    palette_fragments = palette_control.text()
    palette_rendered = "".join(text for _, text in palette_fragments)
    assert "/dashboard" in palette_rendered
    assert "[new]" in palette_rendered
    assert "Show local registry health" in palette_rendered
    assert any(style == "class:palette.selected.marker" and "❯" in text for style, text in palette_fragments)
    composer_control.buffer.cancel_completion()
    composer_control.buffer.reset()

    tab_binding = next(
        binding
        for binding in captured["key_bindings"].bindings
        if binding.handler.__name__ == "complete_or_focus_scrollback"
    )
    layout = captured["layout"]
    composer_control = layout.current_control
    tab_binding.handler(SimpleNamespace(app=SimpleNamespace(layout=layout)))
    assert layout.current_control is not composer_control
    tab_binding.handler(SimpleNamespace(app=SimpleNamespace(layout=layout)))
    assert layout.current_control is composer_control

    welcome_buffer.set_document(
        Document("\n".join(f"line {index}" for index in range(40))),
        bypass_readonly=True,
    )
    welcome_buffer.cursor_position = len(welcome_buffer.text)
    layout.focus(welcome_control)
    row_before = welcome_buffer.document.cursor_position_row
    scroll_up_binding = next(
        binding
        for binding in captured["key_bindings"].bindings
        if binding.handler.__name__ == "scroll_line_up"
    )
    scroll_up_binding.handler(SimpleNamespace(app=SimpleNamespace(layout=layout)))
    assert welcome_buffer.document.cursor_position_row == row_before - 1


def test_fullscreen_terminal_failure_degrades_to_plain_shell(monkeypatch):
    import prompt_toolkit.application

    no_console_error = type("NoConsoleScreenBufferError", (Exception,), {})

    def fail_application(**kwargs):
        raise no_console_error("no console screen buffer")

    monkeypatch.setattr(prompt_toolkit.application, "Application", fail_application)
    output = StringIO()
    code = run_tui(
        input_stream=StringIO("/quit\n"),
        output=output,
        is_tty=True,
        environ={"TERM": "xterm-256color"},
        dispatcher=CommandDispatcher(),
    )

    assert code == 0
    assert "IONIC ESSENTIAL" in output.getvalue()
