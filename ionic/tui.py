"""Interactive terminal surface for Ionic.

THESIS: Ionic is a local contract console, not a chat-agent imitation.
OWN-WORLD: Near-black terminal space, off-white text, Ionic cyan focus, a
responsive ASCII node-link/wordmark, thin context rails, and one outlined
composer. STORY: Users see where they are, choose an exact Ionic operation,
read its bounded result, and stay in the keyboard flow. FIRST VIEWPORT: A
Grok-Build-inspired launch card owns the scrollback; workspace context sits in
its border, while the command composer and local-operation state stay docked
below. FORM: Full-screen scrollback plus composer, translated into
Ionic's deterministic slash-command model with no free prompts or shell mode.

This module deliberately owns *no* contract operation. It turns allowlisted
slash commands into arguments for :mod:`ionic.cli`. prompt_toolkit is lazy so
Ionic remains useful in CI and minimal Python installs.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
import os
from pathlib import Path
import re
import shlex
import shutil
import sys
from types import SimpleNamespace
from typing import Any, TextIO
from urllib.parse import urlsplit


CommandInvoker = Callable[[list[str], Mapping[str, str] | None], Any]
MAX_COMMAND_CHARS = 8_192
MAX_TRANSCRIPT_ENTRY_CHARS = 64_000
MAX_TRANSCRIPT_CHARS = 256_000
MAX_TRANSCRIPT_ENTRIES = 100
MAX_HISTORY_ENTRIES = 500
MAX_SESSION_REPOSITORIES = 64
MAX_PATH_COMPLETIONS = 50
MAX_PATH_CHARS = 4_096
MAX_MODEL_CHARS = 200
MAX_API_KEY_CHARS = 16_384

_REPOSITORY_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")
_DIRECT_REVIEW_PROVIDERS = frozenset({"anthropic", "openai", "google", "xai", "local"})
_REVIEW_CREDENTIAL_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "google": "GEMINI_API_KEY",
    "xai": "XAI_API_KEY",
    "local": "IONIC_LOCAL_API_KEY",
}
_REVIEW_DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-5",
    "openai": "gpt-5.2",
    "google": "gemini-3.6-flash",
    "xai": "grok-4.5",
    "local": "qwen2.5-coder",
}
_SUBSCRIPTION_RUNTIMES = frozenset({"openai-codex", "xai-grok-build"})
_SUBSCRIPTION_DISCLOSURES = {
    "openai-codex": (
        "OpenAI's official Codex app-server owns ChatGPT sign-in and tokens; Ionic "
        "does not receive your password, browser cookies, OAuth token, or API key. "
        "Only an explicit /semantic check sends the compared contract text, proposed "
        "changes, relevant dependency context, and Ionic's review schema/instructions. "
        "Ionic requests an ephemeral thread with restricted readable roots, no inherited "
        "instruction sources, no tool approvals, and network-disabled tool policy."
    ),
    "xai-grok-build": (
        "xAI's official local Grok CLI owns its login and token; Ionic does not request "
        "or store that token. Only an explicit /semantic check sends the compared "
        "contract text, proposed changes, relevant dependency context, and Ionic's review "
        "instructions/schema. Ionic runs the review from an empty temporary folder with "
        "filesystem, terminal, and MCP tools disabled; the Grok CLI may still apply its "
        "own user or administrator configuration."
    ),
}


_FULL_MARK = (
    "  .------.   .------.  ",
    " / ###### \\=/ .--.  \\",
    "| ######## | |    | |",
    " \\ ###### /=\\ '--'  /",
    "  '------'   '------'  ",
)
_TERMINAL_MARK = "●═○"
_UNICODE_MARK = (
    "          •••••••••              ·········",
    "      ••••         ••••      ····○○○○○○○○○····",
    "   •••    ●●●●●●●●●    •••···○○○○         ○○○○···",
    "  •••  ●●●●●●●●●●●●●●●  •···○○               ○○···",
    " ••   ●●●●●●●●●●●●●●●●● ··○○                   ○○··",
    " ••  ●●●●●●●●●●●●●●●●●●●●○○                   ○○··",
    " ••  ●●●●●●●●●●●●●●●●●●●●○○                   ○○··",
    " ••   ●●●●●●●●●●●●●●●●● ··○○                   ○○··",
    "  •••  ●●●●●●●●●●●●●●●  •···○○               ○○···",
    "   •••    ●●●●●●●●●    •••···○○○○         ○○○○···",
    "      ••••         ••••      ····○○○○○○○○○····",
    "          •••••••••              ·········",
)
_FULL_WORDMARK = (
    " ___   ___   _  _   ___   ___ ",
    "|_ _| / _ \\ | \\| | |_ _| / __|",
    " | | | (_) || .` |  | | | (__ ",
    "|___| \\___/ |_|\\_| |___| \\___|",
)
_COMPACT_BRAND = "(###)==( )  IONIC"


@dataclass(frozen=True)
class CommandSpec:
    """Discoverable metadata for one safe command-bar operation."""

    command: str
    description: str
    badge: str = ""
    insertion: str = ""

    @property
    def completion(self) -> str:
        """Text inserted by the command palette, always ready for arguments."""
        return self.insertion or f"{self.command} "


_COMMAND_SPECS = (
    CommandSpec(
        "/dashboard",
        "Show local registry health, counts, and drift",
        "new",
    ),
    CommandSpec(
        "/repo add",
        "Add an existing directory to this TUI session",
        "session",
        "/repo add ",
    ),
    CommandSpec(
        "/repo list",
        "List repositories selected for this TUI session",
        "session",
    ),
    CommandSpec(
        "/repo select",
        "Focus one session repository",
        "session",
        "/repo select ",
    ),
    CommandSpec(
        "/repo remove",
        "Remove a repository from this TUI session",
        "session",
        "/repo remove ",
    ),
    CommandSpec(
        "/semantic status",
        "Show semantic access, provider, model, and credential readiness",
        "review",
    ),
    CommandSpec(
        "/semantic api",
        "Choose an API provider and model for this TUI session",
        "session",
        "/semantic api ",
    ),
    CommandSpec(
        "/semantic subscription",
        "Choose an official subscription runtime for this TUI session",
        "session",
        "/semantic subscription ",
    ),
    CommandSpec(
        "/semantic consent",
        "Review and explicitly accept subscription data access for this session",
        "consent",
    ),
    CommandSpec(
        "/semantic key set",
        "Enter one provider API key in a masked session-only prompt",
        "masked",
        "/semantic key set ",
    ),
    CommandSpec(
        "/semantic key clear",
        "Forget one provider API key for this TUI session",
        "session",
        "/semantic key clear ",
    ),
    CommandSpec(
        "/semantic check",
        "Run one explicitly opted-in semantic contract review",
        "remote",
        "/semantic check ",
    ),
    CommandSpec("/status", "Inspect registry health and drift"),
    CommandSpec("/list", "List registered contracts"),
    CommandSpec("/show", "Inspect one contract"),
    CommandSpec("/check", "Check a proposed contract change"),
    CommandSpec("/drift", "Find changed recorded sources"),
    CommandSpec("/graph", "Show dependency relationships"),
    CommandSpec("/history", "Show recorded revisions"),
    CommandSpec("/diff", "Compare revisions or a file"),
    CommandSpec("/register", "Register a file or directory"),
    CommandSpec("/init", "Initialise a project registry"),
    CommandSpec("/workspace check", "Check a multi-repository workspace"),
    CommandSpec("/workspace scan", "Discover contracts across repositories"),
    CommandSpec("/workspace sync", "Synchronise a workspace registry"),
    CommandSpec("/runtime status", "Inspect subscription runtime availability"),
    CommandSpec("/export", "Export a contract bundle"),
    CommandSpec("/import", "Import a contract bundle"),
    CommandSpec("/extract", "Extract a contract from instructions"),
    CommandSpec("/rm", "Remove a registered contract"),
    CommandSpec("/version", "Print the Ionic version"),
    CommandSpec("/help", "Open the Ionic command guide"),
    CommandSpec("/clear", "Clear this session's scrollback"),
    CommandSpec("/quit", "Leave Ionic"),
)


@dataclass(frozen=True)
class DispatchResult:
    """The result of one interactive submission.

    ``kind`` is ``command``, ``error``, ``secret``, ``clear``, or ``exit``.  The UI can
    render this without needing to know anything about Click, Typer, or Rich.
    """

    kind: str
    output: str = ""
    exit_code: int = 0
    argv: tuple[str, ...] = ()


@dataclass(frozen=True)
class SessionRepository:
    """One repository selected for the current TUI process only."""

    repository_id: str
    path: Path


@dataclass
class ShellState:
    """Small, UI-toolkit-independent state container for a TUI session."""

    transcript: list[str] = field(default_factory=list)
    history: list[str] = field(default_factory=list)

    def append(self, line: str, result: DispatchResult) -> None:
        if result.kind == "clear":
            self.transcript.clear()
            return
        if result.kind == "secret":
            return
        if line.strip():
            self.history.append(line)
            if len(self.history) > MAX_HISTORY_ENTRIES:
                del self.history[:-MAX_HISTORY_ENTRIES]
        if result.kind == "exit":
            return
        shown = result.output.rstrip()
        if result.exit_code and not shown:
            shown = f"Command exited with status {result.exit_code}."
        command = line.strip()
        if command:
            outcome = "[ok]" if result.exit_code == 0 else f"[error {result.exit_code}]"
            shown = f"> {command}\n{outcome}" + (f"\n{shown}" if shown else "")
        if shown:
            if len(shown) > MAX_TRANSCRIPT_ENTRY_CHARS:
                omitted = len(shown) - MAX_TRANSCRIPT_ENTRY_CHARS
                shown = (
                    f"[output truncated: {omitted} earlier characters omitted]\n"
                    f"{shown[-MAX_TRANSCRIPT_ENTRY_CHARS:]}"
                )
            self.transcript.append(shown)
            while (
                len(self.transcript) > MAX_TRANSCRIPT_ENTRIES
                or sum(len(entry) for entry in self.transcript) > MAX_TRANSCRIPT_CHARS
            ):
                self.transcript.pop(0)

    @property
    def text(self) -> str:
        return "\n\n".join(self.transcript)


# The command bar must never become a shell.  These are Ionic command names,
# not executable names; the actual work remains in ``ionic.cli.app``.
_TOP_LEVEL_COMMANDS = frozenset(
    {
        "init",
        "register",
        "list",
        "show",
        "rm",
        "extract",
        "check",
        "drift",
        "export",
        "import",
        "history",
        "diff",
        "graph",
        "status",
        "version",
        "workspace",
        "runtime",
    }
)
_NESTED_COMMANDS = {
    "workspace": frozenset({"scan", "check", "sync"}),
    "runtime": frozenset({"status"}),
}
_ALIASES = {"ls": "list", "remove": "rm", "q": "quit", "?": "help"}

_COMMAND_HELP = """Ionic commands

  /dashboard [options]    local registry overview (same data as /status)
  /repo add ID PATH       add a directory to this TUI session
  /repo list              list session repositories
  /repo select ID         focus a session repository
  /repo remove ID         remove a session repository
  /semantic status        show semantic-review configuration and readiness
  /semantic api ...       choose an API provider/model for this session
  /semantic subscription ...
                           choose an official subscription runtime
  /semantic key set PROVIDER
                           enter a masked, session-only provider credential
  /semantic key clear PROVIDER
                           forget a session credential
  /semantic check <contract> ...
                           run check with explicit semantic review (--llm)
  /status                 registry health and drift summary
  /list [options]         list registered contracts
  /show <contract>        inspect one contract
  /check <contract> ...   compare a contract with a proposed source
  /drift [options]        find changed recorded sources
  /graph [options]        show dependency relationships
  /history <contract>     show recorded revisions
  /diff <contract> ...    compare contract revisions or a file
  /register <path>        register contracts from a file or directory
  /init [path]            initialise a project registry
  /version                print the Ionic version
  /workspace scan|check|sync ...
  /runtime status

  /clear                  clear this session
  /quit                   leave Ionic

Every operation uses the normal Ionic command implementation. Type
/help <command> for the CLI's detailed options.  The command bar does not run
shell commands."""

_SEMANTIC_HELP = """Semantic review commands

  /semantic status
      Show the active access mode, provider/runtime, model, and credential
      readiness. Values come from this session, then .ionic/config.toml and
      environment variables.

  /semantic api PROVIDER [MODEL] [BASE_URL]
      Select anthropic, openai, google, xai, or local for this TUI session.
      MODEL defaults to Ionic's reviewed provider default. BASE_URL is accepted
      only for local OpenAI-compatible models.

  /semantic subscription RUNTIME [MODEL]
      Select openai-codex or xai-grok-build. Ionic uses the runtime's existing
      login and never asks for its token. A semantic run still requires the
      current explicit data-access consent.

  /semantic consent
      Show the selected runtime's current data-access disclosure and the exact
      versioned acceptance command. Consent is session-only and may be revoked.

  /semantic key set PROVIDER
      Open a masked prompt. The key exists only in this process and is never
      written to history, scrollback, argv, Desktop, or .ionic/config.toml.

  /semantic key clear PROVIDER
      Remove that credential from this TUI session only.

  /semantic check CONTRACT [CHECK OPTIONS]
      Run the normal Ionic check command with --llm. This is the explicit action
      that can send selected contract content to the configured provider.

Workspace scan/check v1 remains structural and offline; it does not offer a
semantic pass. Configuration alone never starts a model request."""

_REPOSITORY_HELP = """Session repository commands

  /repo add ID PATH       add an existing directory to this TUI session
  /repo list              list repositories selected in this session
  /repo select ID         mark one session repository as focused
  /repo remove ID         forget one session repository

Session repositories are not written to Desktop, disk, or the contract
registry. When no explicit --repo or --manifest is supplied, /workspace
scan, check, and sync receive the session repositories as --repo ID=PATH.
Use /register PATH separately when you intend to mutate the contract registry."""


def _brand_lines(width: int) -> list[str]:
    """Return a responsive, ASCII-only Ionic logo and wordmark lockup."""
    if width < 64:
        return [_COMPACT_BRAND]
    rows = max(len(_FULL_MARK), len(_FULL_WORDMARK))
    mark = (*_FULL_MARK, *("" for _ in range(rows - len(_FULL_MARK))))
    word = (*_FULL_WORDMARK, *("" for _ in range(rows - len(_FULL_WORDMARK))))
    mark_width = max(map(len, _FULL_MARK))
    word_width = max(map(len, _FULL_WORDMARK))
    return [
        f"{mark[index].ljust(mark_width)}   {word[index].ljust(word_width)}"
        for index in range(rows)
    ]


def banner(width: int | None = None) -> str:
    """Return the ASCII Ionic lockup, centered when the terminal has room."""
    if width is None:
        width = shutil.get_terminal_size(fallback=(80, 24)).columns
    width = max(20, width)
    lines = _brand_lines(width)
    return "\n".join(line.center(width).rstrip() for line in lines)


def _workspace_label(max_width: int = 52) -> str:
    """Return a display-only cwd label without probing Git or the registry."""
    try:
        label = str(Path.cwd())
    except OSError:
        return "."
    return _clip_display_suffix(label, max_width)


def _clip_display_suffix(value: str, max_width: int) -> str:
    """Keep a path suffix within exact terminal cells, including CJK/emoji."""
    from wcwidth import wcswidth

    max_width = max(0, max_width)
    if wcswidth(value) <= max_width:
        return value
    marker = "..."[:max_width]
    if max_width <= len(marker):
        return marker
    for start in range(len(value)):
        candidate = f"{marker}{value[start:]}"
        if wcswidth(candidate) <= max_width:
            return candidate
    return marker


def welcome_screen(
    width: int | None = None,
    height: int | None = None,
    *,
    box_drawing: bool = False,
) -> str:
    """Render the responsive first-view card used before the first command."""
    from . import __version__

    if width is None:
        width = shutil.get_terminal_size(fallback=(80, 24)).columns
    width = max(20, width)

    def top_border(left_pad: str, inner_width: int) -> str:
        if not box_drawing:
            return f"{left_pad}+{'-' * inner_width}+"
        from wcwidth import wcswidth

        workspace = _workspace_label(max(8, inner_width - 4))
        caption = f"─ {workspace} "
        if wcswidth(caption) > inner_width:
            workspace = _clip_display_suffix(workspace, max(0, inner_width - 3))
            caption = f"─ {workspace} "
        caption_width = max(0, wcswidth(caption))
        return f"{left_pad}╭{caption}{'─' * max(0, inner_width - caption_width)}╮"

    if width < 42:
        if box_drawing and width >= 28:
            lines = [
                f"{_TERMINAL_MARK}  IONIC ESSENTIAL  {__version__}",
                "",
                "local contract control",
                "",
                "/dashboard registry overview",
                "/repo add  select repository",
                "/help     all commands",
            ]
        elif box_drawing:
            lines = [
                f"{_TERMINAL_MARK}  IONIC",
                f"ESSENTIAL {__version__}",
                "",
                "contract control",
                "",
                "/dashboard registry",
                "/help commands",
            ]
        elif width < 24:
            lines = [
                banner(width),
                "",
                "contract control",
                "",
                "/dashboard registry",
                "/repo add path",
                "/help commands",
            ]
        else:
            lines = [
                banner(width),
                "",
                "local contract control",
                "",
                "/dashboard registry overview",
                "/repo add  select repository",
                "/help     all commands",
            ]
        return "\n".join(line[:width] for line in lines)

    compact = width < 134 or (height is not None and height < 23)
    if compact:
        box_width = min(width - 6, 66)
        inner_width = box_width - 2
        left_pad = " " * max(0, (width - box_width) // 2)

        def compact_row(text: str = "") -> str:
            clipped = text[: inner_width - 2]
            edge = "│" if box_drawing else "|"
            return f"{left_pad}{edge} {clipped.ljust(inner_width - 2)} {edge}"

        top = top_border(left_pad, inner_width)
        bottom = (
            f"{left_pad}╰{'─' * inner_width}╯"
            if box_drawing
            else top
        )
        identity_rows = (
            [
                compact_row(
                    f"{_TERMINAL_MARK}  IONIC ESSENTIAL  {__version__}".center(
                        inner_width - 2
                    )
                )
            ]
            if box_drawing
            else [
                compact_row(_COMPACT_BRAND.center(inner_width - 2)),
                compact_row(f"IONIC ESSENTIAL  {__version__}".center(inner_width - 2)),
            ]
        )
        card = [
            top,
            compact_row(),
            *identity_rows,
            compact_row("local contract control".center(inner_width - 2)),
            compact_row(),
            compact_row("/dashboard       registry overview"),
            compact_row("/repo add ID PATH  session repository"),
            compact_row("/workspace scan  discover files"),
            compact_row("/help            all commands"),
            compact_row(),
            bottom,
        ]
        if height is not None:
            available = max(0, height - 6)
            card = [""] * max(0, (available - len(card)) // 3) + card
        return "\n".join(card)

    box_width = min(width - 8, 124, max(76, round(width * 0.76)))
    inner_width = box_width - 2
    left_pad = " " * max(0, (width - box_width) // 2)

    def row(text: str = "") -> str:
        clipped = text[: inner_width - 2]
        edge = "│" if box_drawing else "|"
        return f"{left_pad}{edge} {clipped.ljust(inner_width - 2)} {edge}"

    mark_lines = _UNICODE_MARK if box_drawing else _FULL_MARK
    mark_width = max(map(len, mark_lines))
    copy = (
        f"IONIC ESSENTIAL  {__version__}",
        "Local contract control",
        "Exact operations. No shell.",
        "",
        "/dashboard        Registry overview",
        "/repo add ID PATH Session repository",
        "/workspace scan   Discover files",
        "/help             Command guide",
    )
    from wcwidth import wcswidth

    copy_width = max(wcswidth(line) for line in copy)
    block_width = mark_width + 2 + copy_width
    content_pad = max(2, (inner_width - 2 - block_width) // 2)
    content = [row()]
    for index in range(max(len(mark_lines), len(copy))):
        mark = mark_lines[index] if index < len(mark_lines) else ""
        detail = copy[index] if index < len(copy) else ""
        content.append(
            row(f"{' ' * content_pad}{mark.ljust(mark_width)}  {detail}".rstrip())
        )
    content.append(row())
    top = top_border(left_pad, inner_width)
    bottom = (
        f"{left_pad}╰{'─' * inner_width}╯"
        if box_drawing
        else top
    )
    card = [top, *content, bottom]
    if height is not None:
        available = max(0, height - 6)
        card = [""] * max(0, (available - len(card)) // 3) + card
    return "\n".join(card)


def session_header(width: int | None = None) -> str:
    """Return the first-view masthead without opening a registry as a side effect."""
    from . import __version__

    if width is None:
        width = shutil.get_terminal_size(fallback=(80, 24)).columns
    status = f"v{__version__}  local registry  semantic review: opt-in  telemetry: none"
    return f"{banner(width)}\n{status}"


def operation_boundary_text() -> str:
    """Truthful compact disclosure for the persistent TUI status rail."""
    return "registry local · semantic review opt-in · no Ionic telemetry · shell disabled"


def command_candidates(prefix: str = "") -> list[str]:
    """Return deterministic slash-command completion candidates.

    Contract IDs intentionally are not read here: tab completion must not
    create or open a registry as a side effect.
    """
    normalized = prefix.strip().lower()
    return [spec.command for spec in _COMMAND_SPECS if spec.command.startswith(normalized)]


def command_specs(prefix: str = "") -> list[CommandSpec]:
    """Return completion metadata without reading project or registry state."""
    normalized = prefix.strip().lower()
    return [spec for spec in _COMMAND_SPECS if spec.command.startswith(normalized)]


def _clip_display_prefix(value: str, max_width: int) -> str:
    """Clip text to terminal cells while retaining its meaningful prefix."""
    from wcwidth import wcwidth, wcswidth

    max_width = max(0, max_width)
    if wcswidth(value) <= max_width:
        return value
    marker = "..."
    if max_width <= len(marker):
        return marker[:max_width]
    available = max_width - len(marker)
    used = 0
    kept: list[str] = []
    for character in value:
        cells = max(0, wcwidth(character))
        if used + cells > available:
            break
        kept.append(character)
        used += cells
    return f"{''.join(kept)}{marker}"


def _pad_display(value: str, width: int) -> str:
    """Right-pad a string by terminal cells, not Python code points."""
    from wcwidth import wcswidth

    clipped = _clip_display_prefix(value, width)
    return f"{clipped}{' ' * max(0, width - max(0, wcswidth(clipped)))}"


def _path_completion_slot(text: str) -> tuple[int, str, bool] | None:
    """Return a bounded, explicitly path-bearing command-bar slot."""
    patterns = (
        (r"^/repo\s+add\s+[a-z0-9][a-z0-9._-]{0,63}\s+(?P<path>.+)$", True),
        (r"^/register\s+(?P<path>.+)$", False),
    )
    for pattern, directories_only in patterns:
        match = re.match(pattern, text, flags=re.IGNORECASE)
        if match:
            fragment = match.group("path")
            return match.start("path"), fragment, directories_only
    return None


def _quote_command_token(value: str) -> str:
    """Quote one locally discovered path without shell interpretation."""
    if not any(character.isspace() for character in value):
        return value
    return f'"{value}"'


def _local_path_candidates(
    fragment: str,
    *,
    base_path: Path,
    directories_only: bool,
) -> list[tuple[str, str]]:
    """List one directory level for a known path slot, with strict bounds."""
    source = fragment.strip()
    if source.startswith(('"', "'")):
        source = source[1:]
    if source.endswith(('"', "'")):
        source = source[:-1]
    if not source or len(source) > MAX_PATH_CHARS or source.startswith("\\\\"):
        return []

    expanded = os.path.expanduser(source)
    typed = Path(expanded)
    trailing_separator = expanded.endswith((os.sep, os.altsep or os.sep))
    parent_input = typed if trailing_separator else typed.parent
    name_prefix = "" if trailing_separator else typed.name
    lookup = parent_input if parent_input.is_absolute() else base_path / parent_input
    try:
        entries = sorted(
            lookup.iterdir(),
            key=lambda entry: (not entry.is_dir(), entry.name.casefold()),
        )
    except (OSError, PermissionError):
        return []

    candidates: list[tuple[str, str]] = []
    for entry in entries:
        try:
            is_directory = entry.is_dir()
        except OSError:
            continue
        if directories_only and not is_directory:
            continue
        if not entry.name.casefold().startswith(name_prefix.casefold()):
            continue
        candidate_path = parent_input / entry.name
        candidate = str(candidate_path)
        insertion = _quote_command_token(candidate) + " "
        candidates.append((insertion, entry.name + (os.sep if is_directory else "")))
        if len(candidates) >= MAX_PATH_COMPLETIONS:
            break
    return candidates


def _split_command(source: str) -> list[str]:
    """Split command-bar text without treating Windows path separators as escapes."""
    lexer = shlex.shlex(source, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    lexer.escape = ""
    return list(lexer)


def _review_model(value: str) -> str:
    model = value.strip()
    if not model or len(model) > MAX_MODEL_CHARS:
        raise ValueError(f"model must contain 1 to {MAX_MODEL_CHARS} characters")
    if any(ord(character) < 32 or ord(character) == 127 for character in model):
        raise ValueError("model must not contain control characters")
    return model


def _review_local_url(value: str) -> str:
    endpoint = value.strip()
    if len(endpoint) > 2_048:
        raise ValueError("local endpoint is too long")
    try:
        parsed = urlsplit(endpoint)
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("local endpoint is not a valid URL") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("local endpoint must use HTTP or HTTPS and include a host")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("local endpoint must not contain credentials, a query, or a fragment")
    return endpoint


def _terminal_title_label(value: str, *, limit: int = 48) -> str:
    """Return a short control-free label suitable for a terminal tab."""
    cleaned = " ".join(
        "".join(character for character in str(value) if ord(character) >= 32 and ord(character) != 127).split()
    )
    if not cleaned:
        return "registry"
    return cleaned if len(cleaned) <= limit else f"{cleaned[: limit - 1]}…"


def _current_console_title() -> str | None:
    """Capture the Windows console title so the TUI can restore it on exit."""
    if os.name != "nt":
        return None
    try:
        import ctypes

        buffer = ctypes.create_unicode_buffer(1_024)
        ctypes.windll.kernel32.GetConsoleTitleW(buffer, len(buffer))
        return buffer.value
    except (AttributeError, OSError, ValueError):
        return None


def _plain_terminal_output(value: str) -> str:
    """Remove styling codes before rendering CLI output inside TextArea."""
    from rich.text import Text

    return Text.from_ansi(value).plain


def _default_invoke(argv: list[str], environ: Mapping[str, str] | None) -> tuple[int, str]:
    """Run the existing Typer application while capturing a session entry."""
    # Imports are local to prevent a cli -> tui integration from forming an
    # import cycle, and to keep importing ``ionic.tui`` inexpensive.
    from typer.testing import CliRunner

    from .cli import app

    result = CliRunner().invoke(app, argv, env=dict(environ or {}), color=False)
    return result.exit_code, _plain_terminal_output(result.output)


def _result_from_invocation(value: Any, argv: list[str]) -> DispatchResult:
    if isinstance(value, DispatchResult):
        return value
    if isinstance(value, tuple) and len(value) == 2:
        code, output = value
        return DispatchResult("command", str(output), int(code), tuple(argv))
    code = getattr(value, "exit_code", None)
    output = getattr(value, "stdout", getattr(value, "output", None))
    if code is None or output is None:
        raise TypeError("command invoker must return (exit_code, output) or a Click result")
    return DispatchResult("command", str(output), int(code), tuple(argv))


class CommandDispatcher:
    """Programmatic slash-command dispatcher used by the interactive shell.

    Pass a small ``invoker(argv, environ)`` in tests or host integrations.  By
    default it uses ``typer.testing.CliRunner`` against Ionic's actual app.
    """

    def __init__(
        self,
        *,
        invoker: CommandInvoker | None = None,
        environ: Mapping[str, str] | None = None,
        base_path: Path | None = None,
    ) -> None:
        self._invoker = invoker or _default_invoke
        self._environ = dict(environ if environ is not None else os.environ)
        self._base_path = (base_path or Path.cwd()).resolve()
        self._repositories: dict[str, SessionRepository] = {}
        self._selected_repository_id: str | None = None
        self._subscription_consent_runtime: str | None = None

    @property
    def repositories(self) -> tuple[SessionRepository, ...]:
        """Return the current process-local repository selection."""
        return tuple(self._repositories.values())

    @property
    def selected_repository_id(self) -> str | None:
        return self._selected_repository_id

    @property
    def base_path(self) -> Path:
        return self._base_path

    @property
    def terminal_title(self) -> str:
        """Short live context for the terminal tab, without exposing a full path."""
        if self._selected_repository_id:
            context = f"repo: {self._selected_repository_id}"
        else:
            context = f"registry: {_terminal_title_label(self._base_path.name)}"
        return f"Ionic - {context}"

    def set_session_credential(self, provider: str, secret: str) -> DispatchResult:
        """Keep one provider key in this process only, never in argv or storage."""
        selected = provider.strip().lower()
        if selected not in _DIRECT_REVIEW_PROVIDERS:
            return DispatchResult(
                "error",
                "Credential provider must be anthropic, openai, google, xai, or local.",
                2,
            )
        value = secret.strip()
        if not value:
            return DispatchResult("error", "API key cannot be empty. Press Esc to cancel.", 2)
        if len(value) > MAX_API_KEY_CHARS:
            return DispatchResult(
                "error", f"API key exceeds {MAX_API_KEY_CHARS} characters.", 2
            )
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            return DispatchResult("error", "API key contains invalid control characters.", 2)
        self._environ[_REVIEW_CREDENTIAL_ENV[selected]] = value
        return DispatchResult(
            "command",
            f"{selected} credential is configured for this TUI session only.\n"
            "It was not written to history, scrollback, argv, Desktop, or disk.",
        )

    def _clear_session_credential(self, provider: str) -> DispatchResult:
        selected = provider.strip().lower()
        if selected not in _DIRECT_REVIEW_PROVIDERS:
            return DispatchResult(
                "error",
                "Credential provider must be anthropic, openai, google, xai, or local.",
                2,
            )
        names = [_REVIEW_CREDENTIAL_ENV[selected]]
        if selected == "google":
            names.append("GOOGLE_API_KEY")
        for name in names:
            self._environ.pop(name, None)
        return DispatchResult(
            "command",
            f"{selected} credential is unavailable to this TUI session.\n"
            "No Desktop credential or configuration file was changed.",
        )

    def _invoke(self, argv: list[str]) -> DispatchResult:
        try:
            result = _result_from_invocation(self._invoker(argv, self._environ), argv)
            safe_output = self._redact_review_credentials(result.output)
            return result if safe_output == result.output else replace(result, output=safe_output)
        except KeyboardInterrupt:
            return DispatchResult("error", "Command cancelled.", 130, tuple(argv))
        except Exception as exc:  # pragma: no cover - host integration guard
            return DispatchResult(
                "error",
                f"Ionic could not run this command: {self._redact_review_credentials(str(exc))}",
                1,
                tuple(argv),
            )

    def _redact_review_credentials(self, text: str) -> str:
        safe = text
        names = {
            *_REVIEW_CREDENTIAL_ENV.values(),
            "GOOGLE_API_KEY",
            "ANTHROPIC_AUTH_TOKEN",
        }
        for name in names:
            value = self._environ.get(name)
            if value:
                safe = safe.replace(value, "[REDACTED]")
        return safe

    def _repository_listing(self) -> str:
        if not self._repositories:
            return "Session repositories: none. Add one with /repo add ID PATH."
        rows = ["Session repositories (not persisted):"]
        for repository in self._repositories.values():
            marker = "*" if repository.repository_id == self._selected_repository_id else " "
            rows.append(f"  {marker} {repository.repository_id} = {repository.path}")
        rows.append("* focused repository" if self._selected_repository_id else "No focused repository.")
        return "\n".join(rows)

    def _dispatch_repository(self, argv: list[str]) -> DispatchResult:
        if len(argv) == 1 or (len(argv) == 2 and argv[1] in {"--help", "help"}):
            return DispatchResult("command", _REPOSITORY_HELP)
        action = argv[1].lower()
        if action == "list" and len(argv) == 2:
            return DispatchResult("command", self._repository_listing())
        if action == "add":
            if len(argv) != 4:
                return DispatchResult(
                    "error", "Usage: /repo add ID PATH (quote paths containing spaces).", 2
                )
            repository_id, raw_path = argv[2], argv[3]
            if not _REPOSITORY_ID.fullmatch(repository_id):
                return DispatchResult(
                    "error",
                    "Repository ID must be lowercase and use only a-z, 0-9, ., _, or - (max 64).",
                    2,
                )
            if repository_id in self._repositories:
                return DispatchResult("error", f"Repository ID already exists: {repository_id}.", 2)
            if len(self._repositories) >= MAX_SESSION_REPOSITORIES:
                return DispatchResult(
                    "error",
                    f"This session already has {MAX_SESSION_REPOSITORIES} repositories.",
                    2,
                )
            if len(raw_path) > MAX_PATH_CHARS:
                return DispatchResult(
                    "error", f"Repository path exceeds {MAX_PATH_CHARS} characters.", 2
                )
            candidate = Path(raw_path).expanduser()
            if not candidate.is_absolute():
                candidate = self._base_path / candidate
            try:
                resolved = candidate.resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                return DispatchResult("error", f"Repository path is unavailable: {exc}", 2)
            if not resolved.is_dir():
                return DispatchResult("error", f"Repository path is not a directory: {resolved}", 2)

            resolved_key = os.path.normcase(str(resolved))
            for existing in self._repositories.values():
                existing_key = os.path.normcase(str(existing.path))
                try:
                    common = os.path.commonpath((resolved_key, existing_key))
                except ValueError:
                    continue
                if common in {resolved_key, existing_key}:
                    return DispatchResult(
                        "error",
                        f"Repository overlaps session entry {existing.repository_id}: {existing.path}",
                        2,
                    )
            repository = SessionRepository(repository_id, resolved)
            self._repositories[repository_id] = repository
            if self._selected_repository_id is None:
                self._selected_repository_id = repository_id
            return DispatchResult(
                "command",
                f"Added {repository_id} for this TUI session only.\n{resolved}\n"
                "No files, registry entries, or Desktop settings were changed.",
            )
        if action == "select":
            if len(argv) != 3:
                return DispatchResult("error", "Usage: /repo select ID.", 2)
            repository_id = argv[2]
            if repository_id not in self._repositories:
                return DispatchResult("error", f"Unknown session repository: {repository_id}.", 2)
            self._selected_repository_id = repository_id
            return DispatchResult(
                "command", f"Focused {repository_id} for this TUI session."
            )
        if action == "remove":
            if len(argv) != 3:
                return DispatchResult("error", "Usage: /repo remove ID.", 2)
            repository_id = argv[2]
            repository = self._repositories.pop(repository_id, None)
            if repository is None:
                return DispatchResult("error", f"Unknown session repository: {repository_id}.", 2)
            if self._selected_repository_id == repository_id:
                self._selected_repository_id = next(iter(self._repositories), None)
            return DispatchResult(
                "command",
                f"Removed {repository_id} from this TUI session only.\n"
                "The contract registry and repository files were not changed.",
            )
        return DispatchResult(
            "error", "/repo expects one of: add, list, select, remove.", 2
        )

    def _workspace_arguments(self, argv: list[str]) -> list[str]:
        if len(argv) < 2 or argv[1] not in _NESTED_COMMANDS["workspace"]:
            return argv
        explicit_source = any(
            token in {"--repo", "--manifest"}
            or token.startswith(("--repo=", "--manifest="))
            for token in argv[2:]
        )
        if explicit_source or not self._repositories:
            return argv
        enriched = list(argv)
        for repository in self._repositories.values():
            enriched.extend(("--repo", f"{repository.repository_id}={repository.path}"))
        return enriched

    def _dispatch_dashboard(self, argv: list[str]) -> DispatchResult:
        status_argv = ["status", *argv[1:]]
        result = self._invoke(status_argv)
        if result.exit_code or "--help" in status_argv:
            return result
        output = result.output.rstrip()
        overview = self._repository_listing()
        combined = "Ionic dashboard\n" + (f"\n{output}\n" if output else "\n") + f"\n{overview}"
        return DispatchResult("command", combined, 0, tuple(status_argv))

    def _dispatch_semantic(self, argv: list[str]) -> DispatchResult:
        if len(argv) == 1 or (len(argv) == 2 and argv[1] in {"--help", "help"}):
            return DispatchResult("command", _SEMANTIC_HELP)
        action = argv[1].lower()

        if action == "status" and len(argv) == 2:
            result = self._invoke(["status"])
            if result.exit_code:
                return result
            return DispatchResult(
                "command",
                "Semantic review is opt-in per check; configuration alone sends nothing.\n\n"
                + result.output.rstrip(),
                0,
                ("status",),
            )

        if action == "api":
            if not 3 <= len(argv) <= 5:
                return DispatchResult(
                    "error", "Usage: /semantic api PROVIDER [MODEL] [BASE_URL].", 2
                )
            provider = argv[2].lower()
            if provider not in _DIRECT_REVIEW_PROVIDERS:
                return DispatchResult(
                    "error",
                    "API provider must be anthropic, openai, google, xai, or local.",
                    2,
                )
            try:
                model = _review_model(
                    argv[3] if len(argv) >= 4 else _REVIEW_DEFAULT_MODELS[provider]
                )
                endpoint = _review_local_url(argv[4]) if len(argv) == 5 else None
            except ValueError as exc:
                return DispatchResult("error", str(exc), 2)
            if endpoint is not None and provider != "local":
                return DispatchResult(
                    "error", "BASE_URL is accepted only for the local provider.", 2
                )
            self._environ.update(
                IONIC_MODEL_ACCESS="api",
                IONIC_JUDGE_PROVIDER=provider,
                IONIC_JUDGE_MODEL=model,
            )
            if endpoint is not None:
                self._environ["IONIC_LOCAL_BASE_URL"] = endpoint
            credential_names = [_REVIEW_CREDENTIAL_ENV[provider]]
            if provider == "google":
                credential_names.append("GOOGLE_API_KEY")
            if provider == "anthropic":
                credential_names.append("ANTHROPIC_AUTH_TOKEN")
            ready = provider == "local" or any(self._environ.get(name) for name in credential_names)
            readiness = "credential ready" if ready else f"credential missing; use /semantic key set {provider}"
            return DispatchResult(
                "command",
                f"Semantic API session: {provider} · {model}\n{readiness}.\n"
                "No model request was started. Use /semantic check for an explicit review.",
            )

        if action == "subscription":
            if not 3 <= len(argv) <= 4:
                return DispatchResult(
                    "error", "Usage: /semantic subscription RUNTIME [MODEL].", 2
                )
            runtime = argv[2].lower()
            if runtime not in _SUBSCRIPTION_RUNTIMES:
                return DispatchResult(
                    "error", "Runtime must be openai-codex or xai-grok-build.", 2
                )
            try:
                model = _review_model(argv[3]) if len(argv) == 4 else ""
            except ValueError as exc:
                return DispatchResult("error", str(exc), 2)
            self._environ.update(
                IONIC_MODEL_ACCESS="subscription",
                IONIC_SUBSCRIPTION_RUNTIME=runtime,
            )
            self._environ.pop("IONIC_SUBSCRIPTION_CONSENT_VERSION", None)
            self._subscription_consent_runtime = None
            if model:
                self._environ["IONIC_JUDGE_MODEL"] = model
            else:
                self._environ.pop("IONIC_JUDGE_MODEL", None)
            shown_model = model or "runtime default"
            return DispatchResult(
                "command",
                f"Semantic subscription session: {runtime} · {shown_model}\n"
                "No token was requested and no runtime was contacted. An explicit semantic check "
                "still requires the current data-access consent; run /semantic consent.",
            )

        if action == "consent":
            from .config import SUBSCRIPTION_CONSENT_VERSION

            runtime = self._environ.get("IONIC_SUBSCRIPTION_RUNTIME", "").lower()
            if self._environ.get("IONIC_MODEL_ACCESS") != "subscription" or runtime not in _SUBSCRIPTION_RUNTIMES:
                return DispatchResult(
                    "error",
                    "Select openai-codex or xai-grok-build with /semantic subscription first.",
                    2,
                )
            if len(argv) == 2:
                disclosure = _SUBSCRIPTION_DISCLOSURES[runtime]
                return DispatchResult(
                    "command",
                    f"Subscription semantic-review disclosure · {runtime}\n\n"
                    f"{disclosure}\n\n"
                    "Configuration alone sends nothing. Consent lasts only for this TUI process "
                    "and may be revoked with /semantic consent revoke.\n\n"
                    "To accept this exact disclosure, type:\n"
                    f"/semantic consent accept {runtime} {SUBSCRIPTION_CONSENT_VERSION}",
                )
            if len(argv) == 3 and argv[2].lower() == "revoke":
                self._environ.pop("IONIC_SUBSCRIPTION_CONSENT_VERSION", None)
                self._subscription_consent_runtime = None
                return DispatchResult(
                    "command",
                    "Subscription data-access consent was revoked for this TUI session. "
                    "No runtime was contacted.",
                )
            if len(argv) == 5 and argv[2].lower() == "accept":
                accepted_runtime = argv[3].lower()
                accepted_version = argv[4]
                if accepted_runtime != runtime or accepted_version != SUBSCRIPTION_CONSENT_VERSION:
                    return DispatchResult(
                        "error",
                        "Consent must match the selected runtime and the exact current disclosure "
                        "command shown by /semantic consent.",
                        2,
                    )
                self._environ["IONIC_SUBSCRIPTION_CONSENT_VERSION"] = SUBSCRIPTION_CONSENT_VERSION
                self._subscription_consent_runtime = runtime
                return DispatchResult(
                    "command",
                    f"Consent {SUBSCRIPTION_CONSENT_VERSION} accepted for {runtime} in this TUI "
                    "session only. No model request was started; /semantic check remains explicit.",
                )
            return DispatchResult(
                "error",
                "Usage: /semantic consent | /semantic consent accept RUNTIME VERSION | "
                "/semantic consent revoke.",
                2,
            )

        if action == "key":
            if len(argv) != 4 or argv[2].lower() not in {"set", "clear"}:
                return DispatchResult(
                    "error", "Usage: /semantic key set|clear PROVIDER.", 2
                )
            provider = argv[3].lower()
            if provider not in _DIRECT_REVIEW_PROVIDERS:
                return DispatchResult(
                    "error",
                    "Credential provider must be anthropic, openai, google, xai, or local.",
                    2,
                )
            if argv[2].lower() == "clear":
                return self._clear_session_credential(provider)
            return DispatchResult("secret", provider)

        if action == "check":
            if len(argv) < 3:
                return DispatchResult(
                    "error", "Usage: /semantic check CONTRACT [CHECK OPTIONS].", 2
                )
            if "--no-llm" in argv[2:]:
                return DispatchResult(
                    "error", "/semantic check is explicitly semantic; remove --no-llm.", 2
                )
            if self._environ.get("IONIC_MODEL_ACCESS") == "subscription":
                from .config import SUBSCRIPTION_CONSENT_VERSION

                runtime = self._environ.get("IONIC_SUBSCRIPTION_RUNTIME", "").lower()
                consent_is_current = (
                    self._environ.get("IONIC_SUBSCRIPTION_CONSENT_VERSION")
                    == SUBSCRIPTION_CONSENT_VERSION
                )
                consent_matches_selection = self._subscription_consent_runtime == runtime
                if not consent_is_current or not consent_matches_selection:
                    return DispatchResult(
                        "error",
                        "Subscription semantic review requires the current explicit data-access "
                        "consent. Run /semantic consent, review it, then enter its exact acceptance "
                        "command.",
                        2,
                    )
            check_argv = ["check", *argv[2:]]
            if "--llm" not in check_argv:
                check_argv.append("--llm")
            return self._invoke(check_argv)

        return DispatchResult(
            "error", "/semantic expects: status, api, subscription, consent, key, or check.", 2
        )

    def dispatch(self, line: str) -> DispatchResult:
        source = line.strip()
        if not source:
            return DispatchResult("command")
        if len(source) > MAX_COMMAND_CHARS:
            return DispatchResult(
                "error", f"Command is too long (maximum {MAX_COMMAND_CHARS} characters).", 2
            )
        if not source.startswith("/"):
            return DispatchResult(
                "error", "Commands start with /. Try /help; shell commands are not supported.", 2
            )
        try:
            argv = _split_command(source[1:])
        except ValueError as exc:
            return DispatchResult("error", f"Could not parse command: {exc}", 2)
        if not argv:
            return DispatchResult("error", "Type /help to see Ionic commands.", 2)

        argv[0] = _ALIASES.get(argv[0].lower(), argv[0].lower())
        command = argv[0]
        if command == "quit":
            return DispatchResult("exit")
        if command == "clear":
            return DispatchResult("clear")
        if command == "help":
            if len(argv) == 1:
                return DispatchResult("command", _COMMAND_HELP)
            if argv[1].lower() == "repo":
                return DispatchResult("command", _REPOSITORY_HELP)
            if argv[1].lower() == "semantic":
                return DispatchResult("command", _SEMANTIC_HELP)
            argv = [*argv[1:], "--help"]
            command = argv[0].lower()

        if command == "repo":
            return self._dispatch_repository(argv)
        if command == "dashboard":
            return self._dispatch_dashboard(argv)
        if command == "semantic":
            return self._dispatch_semantic(argv)

        if command not in _TOP_LEVEL_COMMANDS:
            return DispatchResult("error", f"Unknown Ionic command: /{command}. Try /help.", 2)
        if command in _NESTED_COMMANDS:
            if len(argv) == 2 and argv[1] == "--help":
                pass
            elif len(argv) < 2 or argv[1].lower() not in _NESTED_COMMANDS[command]:
                allowed = ", ".join(sorted(_NESTED_COMMANDS[command]))
                return DispatchResult("error", f"/{command} expects one of: {allowed}.", 2)
            else:
                argv[1] = argv[1].lower()
        if command == "workspace":
            argv = self._workspace_arguments(argv)
        return self._invoke(argv)


def _plain_loop(
    state: ShellState,
    dispatcher: CommandDispatcher,
    *,
    input_stream: TextIO,
    output: TextIO,
    width: int | None,
) -> int:
    """Portable fallback for NO_COLOR and environments without prompt_toolkit."""
    output.write(f"{welcome_screen(width)}\n")
    output.write(
        "LOCAL REGISTRY | semantic review opt-in | no Ionic telemetry | shell disabled\n"
    )
    output.flush()
    while True:
        output.write("ionic> ")
        output.flush()
        try:
            line = input_stream.readline()
        except KeyboardInterrupt:
            output.write("\nInput cancelled.\n")
            output.flush()
            continue
        if not line:  # Ctrl-D / end of stream
            output.write("\n")
            output.flush()
            return 0
        result = dispatcher.dispatch(line)
        if result.kind == "exit":
            return 0
        if result.kind == "secret":
            provider = result.output
            variable = _REVIEW_CREDENTIAL_ENV.get(provider, "the provider environment variable")
            output.write(
                "Masked credential entry needs the fullscreen TUI. "
                f"Set {variable} before launching Ionic, then try again.\n"
            )
            output.flush()
            continue
        state.append(line, result)
        if result.kind == "clear":
            # Deliberately avoid ANSI clear-screen escapes in the plain mode.
            continue
        if result.output:
            output.write(f"{result.output.rstrip()}\n")
            output.flush()
        elif result.exit_code:
            output.write(f"Command exited with status {result.exit_code}.\n")
            output.flush()


def run_tui(
    *,
    input_stream: TextIO | None = None,
    output: TextIO | None = None,
    is_tty: bool | None = None,
    environ: Mapping[str, str] | None = None,
    dispatcher: CommandDispatcher | None = None,
    width: int | None = None,
) -> int:
    """Run Ionic's interactive shell and return a process-style exit code.

    ``input_stream``, ``output``, ``is_tty``, ``environ``, ``dispatcher``, and
    ``width`` are injectable so embedders can exercise the shell without a real
    terminal. A non-TTY or dumb-terminal session uses the readable line-based
    fallback rather than attempting terminal control sequences. ``NO_COLOR``
    keeps the fullscreen layout and removes styling.
    """
    input_stream = input_stream or sys.stdin
    output = output or sys.stdout
    env = environ if environ is not None else os.environ
    interactive = input_stream.isatty() if is_tty is None else is_tty
    state = ShellState()
    dispatcher = dispatcher or CommandDispatcher(environ=env)

    if not interactive or env.get("TERM", "").lower() == "dumb":
        return _plain_loop(
            state, dispatcher, input_stream=input_stream, output=output, width=width
        )

    try:
        from prompt_toolkit.application.current import get_app
        from prompt_toolkit.application import Application
        from prompt_toolkit.completion import Completer, Completion
        from prompt_toolkit.filters import Condition
        from prompt_toolkit.history import History
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.key_binding.bindings.scroll import (
            scroll_one_line_down as toolkit_scroll_one_line_down,
            scroll_one_line_up as toolkit_scroll_one_line_up,
            scroll_page_down as toolkit_scroll_page_down,
            scroll_page_up as toolkit_scroll_page_up,
        )
        from prompt_toolkit.keys import Keys
        from prompt_toolkit.layout import ConditionalContainer, HSplit, Layout, VSplit, Window
        from prompt_toolkit.layout.controls import FormattedTextControl
        from prompt_toolkit.layout.dimension import Dimension
        from prompt_toolkit.lexers import Lexer
        from prompt_toolkit.mouse_events import MouseEventType
        from prompt_toolkit.styles import Style
        from prompt_toolkit.widgets import TextArea
    except ImportError:
        return _plain_loop(
            state, dispatcher, input_stream=input_stream, output=output, width=width
        )

    class SlashCompleter(Completer):
        def get_completions(self, document: Any, complete_event: Any):
            text = document.text_before_cursor
            if not text.startswith("/"):
                return

            path_slot = _path_completion_slot(text)
            if path_slot is not None:
                if text.endswith((" ", "\t")):
                    return
                start, fragment, directories_only = path_slot
                for insertion, display in _local_path_candidates(
                    fragment,
                    base_path=dispatcher.base_path,
                    directories_only=directories_only,
                ):
                    yield Completion(
                        insertion,
                        start_position=-(len(text) - start),
                        display=display,
                        display_meta="Directory" if directories_only else "Local path",
                    )
                return

            repository_slot = re.match(
                r"^/repo\s+(select|remove)\s+(?P<id>[a-z0-9._-]*)$",
                text,
                flags=re.IGNORECASE,
            )
            if repository_slot:
                prefix = repository_slot.group("id").casefold()
                for repository in dispatcher.repositories:
                    if repository.repository_id.casefold().startswith(prefix):
                        yield Completion(
                            f"{repository.repository_id} ",
                            start_position=-len(repository_slot.group("id")),
                            display=repository.repository_id,
                            display_meta=str(repository.path),
                        )
                return

            if text.endswith((" ", "\t")):
                return
            for spec in command_specs(text):
                metadata = f"[{spec.badge}]  {spec.description}" if spec.badge else spec.description
                yield Completion(
                    spec.completion,
                    start_position=-len(text),
                    display=spec.command,
                    display_meta=metadata,
                )

    class BoundedHistory(History):
        """Session-only prompt history with a strict memory bound."""

        def __init__(self) -> None:
            super().__init__()
            self._storage: list[str] = []

        def load_history_strings(self):
            yield from reversed(self._storage)

        def store_string(self, string: str) -> None:
            self._storage.append(string)
            if len(self._storage) > MAX_HISTORY_ENTRIES:
                del self._storage[:-MAX_HISTORY_ENTRIES]

    class IonicOutputLexer(Lexer):
        """Give the first view and transcript a quiet, semantic hierarchy."""

        _welcome_commands = (
            "/workspace scan",
            "/repo add ID PATH",
            "/dashboard",
            "/help",
        )

        @staticmethod
        def _logo_fragments(value: str):
            """Render equal-weight dots while coloring the two logo halves."""
            fragments: list[tuple[str, str]] = []
            compact_mark = _TERMINAL_MARK in value
            panel_edge = min(
                (index for edge in ("│", "|") if (index := value.find(edge)) >= 0),
                default=0,
            )
            for index, character in enumerate(value):
                displayed = character
                if index < panel_edge:
                    style = "class:transcript"
                elif character in "│|":
                    style = "class:welcome.border"
                elif character == "•":
                    style = "class:welcome.logo.halo.cyan"
                elif character == "·":
                    style = "class:welcome.logo.halo.white"
                    displayed = "•"
                elif character in "●━═":
                    style = "class:welcome.logo.accent"
                elif character == "○":
                    style = "class:welcome.logo.ring"
                    if not compact_mark:
                        displayed = "●"
                else:
                    style = "class:welcome.logo"
                if fragments and fragments[-1][0] == style:
                    previous_style, previous_text = fragments[-1]
                    fragments[-1] = (previous_style, previous_text + displayed)
                else:
                    fragments.append((style, displayed))
            return fragments

        @staticmethod
        def _styled_fragments(style: str, value: str):
            fragments: list[tuple[str, str]] = []
            for character in value:
                fragment_style = (
                    "class:welcome.border" if character in "│|" else style
                )
                if fragments and fragments[-1][0] == fragment_style:
                    previous_style, previous_text = fragments[-1]
                    fragments[-1] = (previous_style, previous_text + character)
                else:
                    fragments.append((fragment_style, character))
            return fragments

        @classmethod
        def _panel_fragments(cls, style: str, value: str):
            margin = len(value) - len(value.lstrip(" "))
            return [
                ("class:transcript", value[:margin]),
                *cls._styled_fragments(style, value[margin:]),
            ]

        def lex_document(self, document: Any):
            welcome = not bool(state.text)

            def fragments(line_number: int):
                try:
                    line = document.lines[line_number]
                except IndexError:
                    return []
                if not line:
                    return [("", "")]
                if not welcome:
                    if line.startswith("> "):
                        return [("class:transcript.command", line)]
                    if line == "[ok]":
                        return [("class:transcript.success", line)]
                    if line.startswith("[error"):
                        return [("class:transcript.error", line)]
                    return [("class:transcript", line)]

                stripped = line.strip()
                if (
                    stripped.startswith("+") and stripped.endswith("+")
                ) or (
                    stripped.startswith(("╭", "╰")) and stripped.endswith(("╮", "╯"))
                ):
                    return self._panel_fragments("class:welcome.border", line)
                for command in self._welcome_commands:
                    index = line.find(command)
                    if index >= 0:
                        return [
                            *self._logo_fragments(line[:index]),
                            ("class:welcome.command", command),
                            *self._styled_fragments(
                                "class:welcome.description",
                                line[index + len(command):],
                            ),
                        ]
                title_index = line.find("IONIC ESSENTIAL")
                if title_index >= 0:
                    title_end = title_index + len("IONIC ESSENTIAL")
                    return [
                        *self._logo_fragments(line[:title_index]),
                        ("class:welcome.title", line[title_index:title_end]),
                        *self._styled_fragments(
                            "class:welcome.description", line[title_end:]
                        ),
                    ]
                accent_index = line.lower().find("local contract control")
                if accent_index >= 0:
                    accent_end = accent_index + len("local contract control")
                    return [
                        *self._logo_fragments(line[:accent_index]),
                        ("class:welcome.accent", line[accent_index:accent_end]),
                        *self._styled_fragments(
                            "class:welcome.description", line[accent_end:]
                        ),
                    ]
                description_index = line.find("Exact operations. No shell.")
                if description_index >= 0:
                    return [
                        *self._logo_fragments(line[:description_index]),
                        *self._styled_fragments(
                            "class:welcome.description", line[description_index:]
                        ),
                    ]
                if any(
                    token in line
                    for token in (
                        "#",
                        ".------.",
                        "'------'",
                        "(###)",
                        "•••",
                        "···",
                        "●",
                        "○",
                    )
                ):
                    return self._logo_fragments(line)
                return self._panel_fragments("class:welcome.description", line)

            return fragments

    terminal_size = shutil.get_terminal_size(fallback=(80, 24))
    original_terminal_title = _current_console_title()
    colors = not bool(env.get("NO_COLOR"))
    output_area = TextArea(
        text=welcome_screen(width, terminal_size.lines, box_drawing=True),
        read_only=True,
        scrollbar=True,
        focusable=True,
        focus_on_click=True,
        wrap_lines=True,
        lexer=IonicOutputLexer(),
        style="class:transcript" if colors else "",
    )
    command_area = TextArea(
        height=1,
        prompt=[("class:prompt", "❯ ")] if colors else "> ",
        multiline=False,
        completer=SlashCompleter(),
        complete_while_typing=True,
        history=BoundedHistory(),
        style="class:composer" if colors else "",
    )
    secret_area = TextArea(
        height=1,
        prompt=[("class:prompt", "key ❯ ")] if colors else "key > ",
        multiline=False,
        password=True,
        complete_while_typing=False,
        style="class:composer" if colors else "",
    )
    bindings = KeyBindings()
    application: Application[Any]
    session_status = {"label": "READY", "detail": "exact Ionic operations"}
    secret_mode: dict[str, str | None] = {"provider": None}
    last_terminal_title: dict[str, str | None] = {"value": None}
    secret_active = Condition(lambda: secret_mode["provider"] is not None)

    def current_columns() -> int:
        try:
            return int(get_app().output.get_size().columns)
        except Exception:
            return width or shutil.get_terminal_size(fallback=(80, 24)).columns

    def current_rows() -> int:
        try:
            return int(get_app().output.get_size().rows)
        except Exception:
            return terminal_size.lines

    def palette_is_visible() -> bool:
        if secret_mode["provider"] is not None:
            return False
        completion_state = command_area.buffer.complete_state
        if completion_state is None or not completion_state.completions:
            return False
        try:
            return get_app().layout.has_focus(command_area)
        except Exception:
            return True

    palette_open = Condition(palette_is_visible)

    def palette_text():
        from wcwidth import wcswidth

        completion_state = command_area.buffer.complete_state
        if completion_state is None or not completion_state.completions:
            return []
        completions = completion_state.completions
        selected = completion_state.complete_index
        if selected is None:
            selected = 0
        visible_count = min(len(completions), 6, max(1, current_rows() - 12))
        start = max(0, min(selected - visible_count // 2, len(completions) - visible_count))
        shown = completions[start : start + visible_count]
        row_width = max(12, current_columns() - 4)
        command_width = min(34, max(10, row_width // 3))
        fragments: list[tuple[str, str]] = []
        for offset, completion in enumerate(shown):
            index = start + offset
            active = index == selected
            marker = "❯ " if active and colors else "> " if active else "  "
            command = completion.display_text
            metadata = completion.display_meta_text
            badge = ""
            badge_match = re.match(r"^\[([^]]+)\]\s*(.*)$", metadata)
            if badge_match:
                badge = f"[{badge_match.group(1)}]"
                metadata = badge_match.group(2)
            marker_style = "class:palette.selected.marker" if active else "class:palette.marker"
            command_style = "class:palette.selected.command" if active else "class:palette.command"
            badge_style = "class:palette.selected.badge" if active else "class:palette.badge"
            meta_style = "class:palette.selected.meta" if active else "class:palette.meta"
            fill_style = "class:palette.selected" if active else "class:palette"

            remaining = row_width - max(0, wcswidth(marker))
            rendered_command = _pad_display(command, min(command_width, remaining))
            remaining -= max(0, wcswidth(rendered_command))
            rendered_badge = ""
            badge_width = max(0, wcswidth(badge))
            if badge and remaining >= badge_width + 1:
                rendered_badge = f" {badge}"
                remaining -= max(0, wcswidth(rendered_badge))
            rendered_meta = ""
            if current_columns() >= 48 and remaining >= 8:
                rendered_meta = "  " + _clip_display_prefix(metadata, max(0, remaining - 2))
                remaining -= max(0, wcswidth(rendered_meta))
            padding = " " * max(0, remaining)

            fragments.extend(
                [
                    (marker_style, marker),
                    (command_style, rendered_command),
                    (badge_style, rendered_badge),
                    (meta_style, rendered_meta),
                    (fill_style, padding),
                ]
            )
            if offset != len(shown) - 1:
                fragments.append((fill_style, "\n"))
        return fragments

    def tip_text():
        try:
            scrollback = get_app().layout.has_focus(output_area)
        except Exception:
            scrollback = False
        columns = current_columns()
        if secret_mode["provider"] is not None:
            provider = secret_mode["provider"]
            if columns < 48:
                return [("class:tip", "  masked · Enter save · Esc cancel")]
            return [
                (
                    "class:tip",
                    f"  {provider} key · masked · session only · Enter saves · Esc cancels",
                )
            ]
        if palette_is_visible():
            if columns < 48:
                return [("class:tip", "  ↑/↓ choose · Enter insert")]
            return [("class:tip", "  ↑/↓ choose · Tab cycles · Enter inserts · Esc closes")]
        if columns < 28:
            return [("class:tip", "  /help · ^D exit")]
        if columns < 48:
            return [("class:tip", "  Tip: /help · Ctrl-D exit")]
        if columns < 72:
            text = "Tip: PgUp/PgDn scroll · Tab command" if scrollback else "Tip: /help commands · Ctrl-D exit"
            return [("class:tip", f"  {text}")]
        if scrollback:
            return [("class:tip", "  Tip: ↑/↓ scroll · PgUp/PgDn pages · g/G jump · Tab returns")]
        return [("class:tip", "  Tip: type / for commands · Tab focuses scrollback · Ctrl-D exits")]

    def composer_top():
        columns = max(20, current_columns())
        return [("class:composer.frame", f"  ╭{'─' * (columns - 6)}╮  ")]

    def composer_bottom():
        columns = max(20, current_columns())
        inside_width = columns - 6
        label = session_status["label"]
        if columns >= 112:
            boundary = " registry local · review opt-in · no telemetry "
        elif columns >= 76:
            boundary = " local registry · no telemetry "
        elif columns >= 36:
            boundary = " registry · shell off "
        else:
            boundary = ""
        left = "─ "
        after_label = " "
        fixed = len(left) + len(label) + len(after_label) + len(boundary)
        if fixed > inside_width:
            boundary = ""
            fixed = len(left) + len(label) + len(after_label) + len(boundary)
        if fixed > inside_width:
            label = label[: max(1, inside_width - len(left) - len(after_label))]
            fixed = len(left) + len(label) + len(after_label)
        fill = "─" * max(0, inside_width - fixed)
        return [
            ("class:composer.frame", "  ╰" + left),
            ("class:status.label", label),
            ("class:composer.frame", after_label + fill),
            ("class:status", boundary),
            ("class:composer.frame", "╯  "),
        ]

    def refresh() -> None:
        output_area.text = (
            state.text
            if state.text
            else welcome_screen(current_columns(), current_rows(), box_drawing=True)
        )
        output_area.buffer.cursor_position = len(output_area.text)

    def prepare_render(app: Any) -> None:
        """Keep the tab context and empty-state geometry synced with live state."""
        title = dispatcher.terminal_title
        set_title = getattr(app.output, "set_title", None)
        if last_terminal_title["value"] != title and callable(set_title):
            set_title(title)
            last_terminal_title["value"] = title
        if state.text:
            return
        size = app.output.get_size()
        desired = welcome_screen(size.columns, size.rows, box_drawing=True)
        if output_area.text != desired:
            output_area.text = desired
            output_area.buffer.cursor_position = len(desired)

    @bindings.add("enter", filter=~palette_open & ~secret_active)
    def submit(event: Any) -> None:
        line = command_area.text
        command_area.buffer.reset(append_to_history=bool(line.strip()))
        result = dispatcher.dispatch(line)
        if result.kind == "exit":
            event.app.exit(result=0)
            return
        if result.kind == "secret":
            secret_mode["provider"] = result.output
            secret_area.buffer.reset()
            session_status.update(label="KEY", detail=f"masked {result.output} credential")
            event.app.layout.focus(secret_area)
            event.app.invalidate()
            return
        state.append(line, result)
        if result.kind == "clear":
            session_status.update(label="READY", detail="session cleared")
        elif result.exit_code:
            session_status.update(
                label=f"ERROR {result.exit_code}", detail="operation needs attention"
            )
        else:
            session_status.update(label="OK", detail="operation completed")
        refresh()

    @bindings.add("enter", filter=secret_active, eager=True)
    def save_masked_credential(event: Any) -> None:
        provider = str(secret_mode["provider"] or "")
        result = dispatcher.set_session_credential(provider, secret_area.text)
        secret_area.buffer.reset()
        if result.exit_code:
            session_status.update(label="ERROR", detail=result.output)
            event.app.invalidate()
            return
        secret_mode["provider"] = None
        state.append(f"/semantic key set {provider}", result)
        session_status.update(label="OK", detail=f"{provider} key ready for this session")
        refresh()
        event.app.layout.focus(command_area)
        invalidate = getattr(event.app, "invalidate", None)
        if callable(invalidate):
            invalidate()

    def cancel_masked_credential(event: Any) -> None:
        secret_area.buffer.reset()
        secret_mode["provider"] = None
        session_status.update(label="READY", detail="credential entry cancelled")
        event.app.layout.focus(command_area)
        event.app.invalidate()

    bindings.add("escape", filter=secret_active, eager=True)(cancel_masked_credential)
    bindings.add("c-c", filter=secret_active, eager=True)(cancel_masked_credential)
    bindings.add("c-d", filter=secret_active, eager=True)(cancel_masked_credential)

    @bindings.add("enter", filter=palette_open, eager=True)
    def apply_palette_selection(event: Any) -> None:
        completion_state = command_area.buffer.complete_state
        if completion_state is None or not completion_state.completions:
            return
        index = completion_state.complete_index
        completion = completion_state.completions[0 if index is None else index]
        command_area.buffer.apply_completion(completion)

    @bindings.add("down", filter=palette_open, eager=True)
    def select_next_palette_row(event: Any) -> None:
        command_area.buffer.complete_next()

    @bindings.add("up", filter=palette_open, eager=True)
    def select_previous_palette_row(event: Any) -> None:
        command_area.buffer.complete_previous()

    @bindings.add("escape", filter=palette_open, eager=True)
    def close_palette(event: Any) -> None:
        command_area.buffer.cancel_completion()

    @bindings.add("c-c", filter=~secret_active)
    def cancel_input(event: Any) -> None:
        command_area.buffer.reset()

    @bindings.add("c-d", filter=~secret_active)
    def exit_shell(event: Any) -> None:
        event.app.exit(result=0)

    @bindings.add("tab", filter=palette_open, eager=True)
    def cycle_palette_forward(event: Any) -> None:
        command_area.buffer.complete_next()

    @bindings.add("s-tab", filter=palette_open, eager=True)
    def cycle_palette_backward(event: Any) -> None:
        command_area.buffer.complete_previous()

    @bindings.add("tab", filter=~palette_open & ~secret_active)
    def complete_or_focus_scrollback(event: Any) -> None:
        if event.app.layout.has_focus(output_area):
            event.app.layout.focus(command_area)
        elif command_area.text:
            command_area.buffer.start_completion(select_first=True)
        else:
            event.app.layout.focus(output_area)

    @bindings.add("s-tab", filter=~palette_open & ~secret_active)
    def focus_previous_pane(event: Any) -> None:
        if event.app.layout.has_focus(output_area):
            event.app.layout.focus(command_area)
        elif command_area.text:
            command_area.buffer.start_completion(select_first=True)
        else:
            event.app.layout.focus(output_area)

    scrollback_focused = Condition(lambda: get_app().layout.has_focus(output_area))

    def move_scrollback_cursor(delta: int) -> None:
        document = output_area.buffer.document
        target_row = max(
            0,
            min(document.line_count - 1, document.cursor_position_row + delta),
        )
        output_area.buffer.cursor_position = document.translate_row_col_to_index(target_row, 0)

    def scroll_output(
        event: Any,
        toolkit_handler: Callable[[Any], None],
        *,
        fallback_delta: int,
        restore_focus: bool,
    ) -> None:
        """Move the rendered viewport immediately, even while the composer is focused."""
        layout = event.app.layout
        previous = layout.current_control
        if not layout.has_focus(output_area):
            layout.focus(output_area)
        if output_area.window.render_info is None:
            move_scrollback_cursor(fallback_delta)
        else:
            toolkit_handler(event)
        if restore_focus and previous is not output_area.control:
            layout.focus(previous)
        invalidate = getattr(event.app, "invalidate", None)
        if callable(invalidate):
            invalidate()

    @bindings.add("pageup")
    def scroll_page_up(event: Any) -> None:
        scroll_output(
            event,
            toolkit_scroll_page_up,
            fallback_delta=-10,
            restore_focus=True,
        )

    @bindings.add("pagedown")
    def scroll_page_down(event: Any) -> None:
        scroll_output(
            event,
            toolkit_scroll_page_down,
            fallback_delta=10,
            restore_focus=True,
        )

    @bindings.add(Keys.ScrollUp, eager=True)
    def scroll_wheel_up(event: Any) -> None:
        scroll_output(
            event,
            toolkit_scroll_one_line_up,
            fallback_delta=-1,
            restore_focus=True,
        )

    @bindings.add(Keys.ScrollDown, eager=True)
    def scroll_wheel_down(event: Any) -> None:
        scroll_output(
            event,
            toolkit_scroll_one_line_down,
            fallback_delta=1,
            restore_focus=True,
        )

    @bindings.add("up", filter=scrollback_focused, eager=True)
    def scroll_line_up(event: Any) -> None:
        scroll_output(
            event,
            toolkit_scroll_one_line_up,
            fallback_delta=-1,
            restore_focus=False,
        )

    @bindings.add("down", filter=scrollback_focused, eager=True)
    def scroll_line_down(event: Any) -> None:
        scroll_output(
            event,
            toolkit_scroll_one_line_down,
            fallback_delta=1,
            restore_focus=False,
        )

    @bindings.add("g", filter=scrollback_focused)
    def scroll_to_top(event: Any) -> None:
        output_area.buffer.cursor_position = 0

    @bindings.add("G", filter=scrollback_focused)
    def scroll_to_bottom(event: Any) -> None:
        output_area.buffer.cursor_position = len(output_area.text)

    palette_window = ConditionalContainer(
        VSplit(
            [
                Window(width=2, char=" ", style="class:surface" if colors else ""),
                Window(
                    content=FormattedTextControl(palette_text),
                    height=Dimension(min=1, max=6),
                    dont_extend_height=True,
                    wrap_lines=False,
                    style="class:palette" if colors else "",
                ),
                Window(width=2, char=" ", style="class:surface" if colors else ""),
            ]
        ),
        filter=palette_open,
    )
    tip_bar = Window(
        height=1,
        content=FormattedTextControl(tip_text),
        style="class:surface" if colors else "",
    )
    composer_middle = VSplit(
        [
            Window(width=2, char=" ", style="class:surface" if colors else ""),
            Window(
                width=1,
                height=1,
                content=FormattedTextControl(
                    [("class:composer.frame" if colors else "", "│")]
                ),
            ),
            ConditionalContainer(command_area, filter=~secret_active),
            ConditionalContainer(secret_area, filter=secret_active),
            Window(
                width=1,
                height=1,
                content=FormattedTextControl(
                    [("class:composer.frame" if colors else "", "│")]
                ),
            ),
            Window(width=2, char=" ", style="class:surface" if colors else ""),
        ],
        height=1,
    )
    composer = HSplit(
        [
            Window(height=1, content=FormattedTextControl(composer_top)),
            composer_middle,
            Window(height=1, content=FormattedTextControl(composer_bottom)),
        ],
        height=3,
    )
    bottom_spacer = ConditionalContainer(
        Window(height=2, char=" ", style="class:surface" if colors else ""),
        filter=Condition(lambda: current_rows() >= 22),
    )
    root = HSplit([output_area, palette_window, tip_bar, composer, bottom_spacer])
    layout = Layout(root, focused_element=command_area)

    def route_mouse_wheel(control: Any) -> None:
        """Make wheel events over docked chrome scroll the transcript too."""
        original = control.mouse_handler

        def mouse_handler(mouse_event: Any) -> Any:
            if mouse_event.event_type in {
                MouseEventType.SCROLL_UP,
                MouseEventType.SCROLL_DOWN,
            }:
                app = get_app()
                upward = mouse_event.event_type == MouseEventType.SCROLL_UP
                scroll_output(
                    SimpleNamespace(app=app),
                    toolkit_scroll_one_line_up if upward else toolkit_scroll_one_line_down,
                    fallback_delta=-1 if upward else 1,
                    restore_focus=True,
                )
                return None
            return original(mouse_event)

        control.mouse_handler = mouse_handler

    for routed_control in layout.find_all_controls():
        if routed_control is not output_area.control:
            route_mouse_wheel(routed_control)
    ui_style = Style.from_dict(
        {
            "surface": "bg:#090909",
            "transcript": "#e8e8e8 bg:#090909",
            "transcript.command": "bold #e8e8e8 bg:#090909",
            "transcript.success": "#62d6a8 bg:#090909",
            "transcript.error": "#ff8f8f bg:#090909",
            "welcome.border": "#606367 bg:#0e0f10",
            "welcome.logo": "#777a7e bg:#0e0f10",
            "welcome.logo.halo.cyan": "#26dbff bg:#0e0f10",
            "welcome.logo.halo.white": "#ffffff bg:#0e0f10",
            "welcome.logo.accent": "bold #26dbff bg:#0e0f10",
            "welcome.logo.ring": "#ffffff bg:#0e0f10",
            "welcome.title": "bold #eeeeee bg:#0e0f10",
            "welcome.accent": "bold #26dbff bg:#0e0f10",
            "welcome.command": "bold #eeeeee bg:#0e0f10",
            "welcome.description": "#8b8d90 bg:#0e0f10",
            "composer": "#f2f2f2 bg:#0b0c0d",
            "composer.frame": "#606367 bg:#090909",
            "prompt": "bold #26dbff bg:#0b0c0d",
            "status": "#8b8d90 bg:#090909",
            "status.label": "bold #26dbff bg:#090909",
            "tip": "#8b8d90 bg:#090909",
            "palette": "#8b8d90 bg:#0e0f10",
            "palette.marker": "#606367 bg:#0e0f10",
            "palette.command": "bold #eeeeee bg:#0e0f10",
            "palette.badge": "#26dbff bg:#0e0f10",
            "palette.meta": "#8b8d90 bg:#0e0f10",
            "palette.selected": "#eeeeee bg:#343638",
            "palette.selected.marker": "bold #26dbff bg:#343638",
            "palette.selected.command": "bold #ffffff bg:#343638",
            "palette.selected.badge": "bold #26dbff bg:#343638",
            "palette.selected.meta": "#b7b9bc bg:#343638",
        }
    ) if colors else None
    application = None
    try:
        application = Application(
            layout=layout,
            key_bindings=bindings,
            full_screen=True,
            mouse_support=True,
            style=ui_style,
            before_render=prepare_render,
        )
        return int(application.run() or 0)
    except Exception as exc:
        # Some terminal-like hosts (notably older mintty/ConEmu bridges and
        # redirected pseudo-TTYs on Windows) report isatty() but expose no
        # console screen buffer. Keep Ionic usable without terminal control.
        if not isinstance(exc, OSError) and type(exc).__name__ != "NoConsoleScreenBufferError":
            raise
        return _plain_loop(
            state, dispatcher, input_stream=input_stream, output=output, width=width
        )
    finally:
        app_output = getattr(application, "output", None)
        if app_output is not None and original_terminal_title is not None:
            app_output.set_title(original_terminal_title)


__all__ = [
    "CommandDispatcher",
    "DispatchResult",
    "ShellState",
    "banner",
    "command_candidates",
    "run_tui",
    "session_header",
]
