"""Bare `omh`: the top-level options and the launch of the OH-MY-HERMES terminal, without the command tree.

`omh.cli.main` reads an argv with only top-level options (`bare_launch_args`)
and runs the launch from here, so opening the terminal imports this module and
what the launch itself needs, not every subcommand module, and builds one
small parser instead of the whole command tree. Anything else, `--help` and
`--version` included, goes to `omh.commands.main`, whose parser takes the same
top-level options from `add_top_level_options`.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from typing import NoReturn

from ..paths import resolve_paths


def _paths(args: argparse.Namespace):
    return resolve_paths(args.omh_home, args.hermes_home, scope=getattr(args, "scope", None))


def add_top_level_options(parser: argparse.ArgumentParser) -> None:
    """The options `omh` takes before any subcommand; a bare launch takes nothing else."""
    parser.add_argument("--omh-home", default=None, help="Override the managed OMH home directory (default: ~/.omh).")
    parser.add_argument("--hermes-home", default=None, help="Override the target Hermes home directory (default: ~/.hermes).")
    parser.add_argument(
        "--scope",
        choices=("user", "project"),
        default=None,
        help="Choose default OMH/Hermes paths when explicit homes are not supplied.",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Print indented JSON for human reading (default is compact; OMH_JSON_PRETTY=1 also works).",
    )
    # Bare-launch pass-through to `hermes`. Hermes accepts all three at its own
    # top level (`hermes_cli/_parser.py`; `--profile`/`-p` is consumed before
    # its argparse runs), and no top-level OMH option claimed `-r`, `-c` or
    # `-p`, so the short forms carry over unchanged.
    #
    # These are the only top-level options a subcommand does NOT honour: they
    # open the terminal and nothing else. `main` rejects them alongside a
    # subcommand rather than ignoring them, because silently dropping `-p`
    # would run that subcommand against a different profile than was asked
    # for. The dests are prefixed because several subcommands define a
    # `--profile` of their own, and a subparser's defaults overwrite the
    # parent's values in the namespace they share.
    parser.add_argument(
        "-r",
        "--resume",
        dest="tui_resume",
        default=None,
        metavar="SESSION",
        help="Bare launch only: reopen this Hermes session id in the terminal.",
    )
    parser.add_argument(
        "-c",
        "--continue",
        dest="tui_continue",
        nargs="?",
        const="",
        default=None,
        metavar="NAME",
        help="Bare launch only: continue the most recent Hermes session, or one named NAME.",
    )
    parser.add_argument(
        "-p",
        "--profile",
        dest="tui_profile",
        default=None,
        metavar="NAME",
        help="Bare launch only: open the terminal on this Hermes bot profile.",
    )


_TUI_PASSTHROUGH_OPTIONS: tuple[tuple[str, str], ...] = (
    ("tui_resume", "--resume"),
    ("tui_continue", "--continue"),
    ("tui_profile", "--profile"),
)


class _NotBare(Exception):
    """The small parser refused the argv; `omh.commands.main`'s parser reads it and says why."""


class _BareParser(argparse.ArgumentParser):
    """Raises `_NotBare` where argparse would print an error and exit.

    `exit_on_error=False` is not enough: Python 3.11 reports an ambiguous
    abbreviation (`omh --p x`) through `error()` itself.
    """

    def error(self, message: str) -> NoReturn:
        raise _NotBare(message)


def bare_launch_args(argv: list[str]) -> argparse.Namespace | None:
    """The parsed top-level options when `argv` is a bare launch; None for anything `omh.commands.main` must read.

    A bare launch is an argv the top-level options account for entirely.
    One that names a subcommand, asks for help or the version, or does not
    parse here is None, and the full parser reads it, errors included, so
    every message is the one it always was: this parser prints nothing.
    """
    parser = _BareParser(prog="omh", add_help=False)
    add_top_level_options(parser)
    try:
        args, unknown = parser.parse_known_args(argv)
    except _NotBare:
        return None
    return None if unknown else args


def run_bare_launch(args: argparse.Namespace) -> int:
    """Open the terminal for a bare launch; the welcome text when there is nothing to launch."""
    launched = _launch_hermes_tui(args)
    if launched is not None:
        return launched
    _print_welcome()
    return 0


def _print_welcome() -> None:
    print(
        """OMH - oh-my-hermes

Install OMH, then talk to Hermes. The `omh` command is the setup, doctor,
update, verifier, and wrapper/backend surface; the normal user experience is
Hermes Agent chat with installed OMH skills.

If this screen appears after `omh uninstall`, the command package is still on
PATH. `uninstall` removes OMH-managed Hermes files and removes the command only
when it can prove the command came from the installer-managed OMH venv.

Start:
  omh setup              Install skills and connect them to Hermes
  omh doctor             Check local OMH health and registration
  omh quickstart         Show what to do next in Hermes
  omh update             Refresh managed skills and update metadata
  omh --version          Print the command version and its build identity

First five minutes:
  1. Run `omh setup` and accept the recommended choices.
  2. Restart or reload Hermes Agent.
  3. Ask Hermes the prompt below.

Useful operator commands:
  omh quickstart         Show first-use prompts and evidence boundaries
  omh recommend "risky refactor"
  omh cases recommend "daily competitor digest"
  omh cases readiness   Check the G1-G10 readiness rollup
  omh cases demo --all  Show wrapper-ready G1-G10 use-case cards
  omh cases artifact --all --write
                          Write local G1-G10 runbook artifacts
  omh cases replay        Replay G1-G10 routing fixtures
  omh context brief "make an image card for this PR"
                          Show compact OMH context and route hint
  omh playbook recommend "turn this issue into a PR"
  omh chat interact "turn this issue into a PR-ready plan"
  omh hud                Show the compact OMH status line
  omh achievements summary
                          Show observed hermes-achievements badges
  omh menubar status     Show the OMH menu bar status summary
  omh mcp manifest       Print the optional stdio MCP bridge manifest
  omh release evidence-bundle --version 1.0.5 --write
                          Write local deterministic release evidence
  omh plugin observe-host --host hermes-agent --session <session-id> --event plugin_load --evidence-ref <host-log>
  omh loop status        Show loopable goal cycle state
  omh ops list           List local operations artifacts
  omh materials list     List material-processing artifacts
  omh paper list         List paper-learning records and where reading stopped
  omh img-summary prompt-card Prepare image-generation-ready summary cards
  omh worktree list      List observed worktree isolation records
  omh worktree bind --path .worktrees/risky-refactor --executor codex --session <session-id>
  omh runtime status     Show local evidence artifacts

After setup, restart or reload Hermes Agent and try:
  Use OMH request-to-handoff for: I want to safely add a feature to this repo.

If `omh` is not found in a new terminal, use the absolute command path printed
by the installer or add that directory to PATH. Run `omh doctor` after that to
verify Hermes registration.

Run `omh --help` for the full command list."""
    )


def _run_startup_update_check(args: argparse.Namespace) -> None:
    """Compare the local install against `origin/main` and act per `omh update-check`.

    Opt-in only: `update_check.mode` defaults to `off`, which returns before
    any network attempt. Runs synchronously, right before exec'ing `hermes`,
    because that exec hands the terminal to a separate process -- there is no
    "after the TUI paints" moment left in this process to report a result in,
    so the whole probe has to fit inside its own bounded timeout ahead of the
    handoff rather than racing it. Any failure here is a silent skip; it must
    never block, delay beyond that bound, or crash the launch.

    The except clause is deliberately wider than `OSError` alone: it is the
    same "corrupt on-disk JSON" shape `local_store.read_json_object_result`
    already classifies, kept here only as a backstop in case a future reader
    on this path stops going through it -- `evaluate_update_check`'s own
    readers no longer raise on a corrupt `setup-profile.json`/state/cache
    file, so a user who never opted into this check must never see a launch
    traceback from one.
    """
    from ..maintenance.update_check import evaluate_update_check, format_notice_line

    paths = _paths(args)
    try:
        result = evaluate_update_check(paths)
    except (OSError, ValueError, json.JSONDecodeError):
        return
    if result.get("should_auto_update"):
        _run_auto_update(args, paths, result)
        return
    # Only a fresh probe prints a notice. Reusing the cache inside the same
    # interval would otherwise reprint the same "behind"/"inconclusive" line
    # on every launch until the interval elapses -- once per interval is the
    # documented cadence (docs/INSTALLATION.md, "Startup update check").
    if result.get("checked"):
        notice = format_notice_line(result)
        if notice:
            print(notice)


# `omh update` streams pip/npm/brew output and can take minutes on the
# preview channel (`commands/setup.py:_run_command_package_self_update`); this
# bounds that wait instead of letting a hung download block the launch
# forever.
_AUTO_UPDATE_SUBPROCESS_TIMEOUT_SECONDS = 600.0


def _auto_update_started_line(result: dict[str, object]) -> str:
    """What `auto` says before it spends the launch on an update.

    `notify` mode already prints why the launch is not current
    (`maintenance/update_check.py:format_notice_line`); `auto` used to print
    nothing at all and simply hold the terminal for however long `omh
    update` took, which reads as a hung launch rather than as a running
    update. Same sentence shape as the `notify` notice, so the two modes
    read as one voice.
    """
    local = str(result.get("local_commit", ""))[:7] or "unknown"
    remote = str(result.get("remote_commit", ""))[:7] or "unknown"
    channel = str(result.get("channel", "")) or "unknown"
    return f"OMH Auto Update: {local} -> {remote} ({channel} channel); running `omh update`."


def _auto_update_complete_line(paths) -> str:
    """What `auto` says once the update subprocess has succeeded.

    The version comes from the record `omh update` just rewrote
    (`commands/setup.py`), not from this process: this process is still
    running the pre-update package, and its own `__version__` would report
    the version the update just replaced. The OMH TUI the launch opens next
    is a new process that imports the updated package, so the line names
    the version its banner is about to show.
    """
    from ..maintenance.update_check_state import read_runtime_state

    version = str(read_runtime_state(paths).get("version", "") or "").strip()
    if version:
        return f"OMH Auto Update complete: omh {version} is installed."
    return "OMH Auto Update complete."


def _run_auto_update(args: argparse.Namespace, paths, result: dict[str, object]) -> None:
    """Auto mode: reuse `omh update`'s own code path, never a reimplementation.

    Spawns `omh update --no-interactive` as a real subprocess rather than
    calling `cmd_update()` in-process. `cmd_update()` can re-enter itself via
    `commands/setup.py:_reentry_argv_with_command_package_updated()`, which
    reads the real process's `sys.argv[1:]` -- not the synthesized
    `update_argv` below. Calling it in-process left `sys.argv` at whatever
    bare `omh` was launched with (empty), so that re-entry became `python -m
    omh.cli --command-package-updated` with no `update` subcommand, exited 2,
    and the post-update half (managed skills, plugin bundle, widgets,
    registration, `release_source_commit`) never ran. A real subprocess gives
    that re-entry its own correct `sys.argv`.

    `--no-interactive` states what this launch is, a background run that
    must never wait on a prompt. Update asks nothing today, so it changes
    nothing; it stays so a prompt added later cannot block a launch.

    The attempt announces itself on stdout and says so again when it lands
    (`_auto_update_started_line`, `_auto_update_complete_line`), because the
    launch is held for the whole subprocess: an unannounced wait of minutes
    is indistinguishable from a hung launch.

    A non-blocking lock keeps two simultaneous launches from auto-updating at
    once; losing the race is a silent skip, not a retry. A failed update is
    reported once (so the user is not left guessing) and never retried before
    the next `evaluate_update_check` interval -- `_run_startup_update_check`
    only reaches here on a fresh "behind" outcome, so a cached one from
    within the same interval never re-triggers this. On success, the cache is
    re-anchored so a launch later in the same interval reads it as resolved
    instead of still "behind".
    """
    from ..installer import OmhError
    from ..local_store import FileLockTimeout
    from ..maintenance.update_check import acquire_auto_update_lock, refresh_cache_after_auto_update

    try:
        with acquire_auto_update_lock(paths):
            # Announced only after the lock is held: a launch that loses the
            # race skips silently and must not claim an update it never ran.
            print(_auto_update_started_line(result))
            # --omh-home/--hermes-home/--scope are TOP-LEVEL parser options
            # (defined before `add_subparsers`), so they must precede the
            # "update" subcommand token, not follow it.
            update_argv: list[str] = []
            if getattr(args, "omh_home", None):
                update_argv += ["--omh-home", str(args.omh_home)]
            if getattr(args, "hermes_home", None):
                update_argv += ["--hermes-home", str(args.hermes_home)]
            if getattr(args, "scope", None):
                update_argv += ["--scope", str(args.scope)]
            update_argv += ["update", "--no-interactive"]
            # Fail fast on a malformed argv rather than handing it to a
            # subprocess that can only report it as an opaque exit code.
            from .main import build_parser  # the whole command tree, only on this rare path

            build_parser().parse_args(update_argv)
            # `-P`: the launch may happen inside a source checkout whose
            # top-level `omh/` shim would otherwise sit at sys.path[0] and run
            # the checkout instead of this installed package.
            completed = subprocess.run(
                [sys.executable, "-P", "-m", "omh.cli", *update_argv],
                timeout=_AUTO_UPDATE_SUBPROCESS_TIMEOUT_SECONDS,
            )
            if completed.returncode == 0:
                refresh_cache_after_auto_update(paths)
                print(_auto_update_complete_line(paths))
            else:
                print(
                    f"omh: update-check auto-update failed (exit {completed.returncode})",
                    file=sys.stderr,
                )
    except FileLockTimeout:
        return
    except (OmhError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"omh: update-check auto-update failed: {exc}", file=sys.stderr)


def _hermes_passthrough_argv(args: argparse.Namespace, *, tui: bool = False) -> list[str]:
    """The bare-launch flags forwarded to `hermes`, in one fixed order.

    Every value is its own argv element: nothing is interpolated into a string
    and nothing goes through a shell. `--tui` goes right after the profile
    that selects the home, for the OMH TUI attempt and for the fallback to
    Hermes' own TUI (`_run_hermes_fallback`).
    """
    argv: list[str] = []
    profile = getattr(args, "tui_profile", None)
    if profile:
        argv += ["--profile", profile]
    if tui:
        argv.append("--tui")
    resume = getattr(args, "tui_resume", None)
    if resume:
        argv += ["--resume", resume]
    continued = getattr(args, "tui_continue", None)
    if continued is not None:
        argv.append("--continue")
        if continued:
            argv.append(continued)
    return argv


def _print_resume_hint(
    args: argparse.Namespace,
    paths,
    started_at: float,
    ended_at: float,
    returncode: int,
) -> None:
    """Print one copyable `omh --resume <id>` line after the TUI's own epilogue.

    Hermes prints its exit summary first, naming `hermes --tui --resume <id>`.
    OMH cannot suppress that without capturing the child's stdout, which would
    break the TUI, so this line follows it and gives someone who came in
    through `omh` the `omh` way back.

    Only the two exit codes Hermes itself treats as a normal close print
    anything, matching the condition its own epilogue uses. Everything else is
    best effort: this runs after the person's session has ended, so it must
    not raise and must not change the code the launcher returns. Whatever it
    cannot resolve, it prints nothing for.

    stdout being a TTY is already settled: `_launch_hermes_tui` returns before
    launching anything when it is not, so no child ran and this is never
    reached.
    """
    if returncode not in (0, 130):
        return
    from ..maintenance.hermes_resume import (
        hermes_state_db_path,
        resumable_session_id,
        resume_command_line,
    )

    profile = getattr(args, "tui_profile", None) or None
    try:
        session_id = resumable_session_id(
            hermes_state_db_path(paths.hermes_home, profile), started_at, ended_at
        )
        if session_id:
            print(resume_command_line(session_id, profile))
    except (OSError, ValueError):
        return


def _launch_hermes_tui(args: argparse.Namespace) -> int | None:
    """Open the OH-MY-HERMES terminal: bare `omh` opens the OMH TUI.

    The oh-my-zsh contract, applied here: the wrapper's bare name IS the
    styled experience. This runs the user's own `hermes` binary with the
    three session flags the bare launch passes through
    (`_hermes_passthrough_argv`) -- a user-invoked local launch, not
    background dispatch. When the OMH TUI can run here (`_run_terminal`), the
    launch adds `--tui` to those flags and `HERMES_TUI_DIR` to the
    environment, so Hermes' launcher starts the OMH frontend in place of its
    own; when it cannot, or it asks for the fallback (`FALLBACK_EXIT` and the
    sentinel file), the launch opens Hermes' own terminal instead
    (`_run_hermes_fallback`). No terminal or no Hermes install means there is
    nothing to launch, and the caller falls back to the welcome text.

    The child's start and end wall-clock times are recorded because they are
    the only handle OMH has on which session closed with it: see
    `maintenance/hermes_resume.py` for why the id cannot be read any other
    way.
    """
    import shutil

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return None
    hermes = shutil.which("hermes")
    if not hermes:
        return None
    _run_startup_update_check(args)
    paths = _paths(args)
    started_at = time.time()
    try:
        returncode = _run_terminal(hermes, args, paths)
    except (OSError, KeyboardInterrupt):
        return None
    _print_resume_hint(args, paths, started_at, time.time(), returncode)
    return returncode


def _omh_tui_blocker(paths) -> str:
    from ..tui.launch import omh_tui_blocker

    return omh_tui_blocker(sys.stdin, sys.stdout, os.environ, paths.hermes_home)


def _tui_omh_home(args: argparse.Namespace, paths):
    """The OMH home the OMH TUI is handed as `OMH_HOME`.

    The one this `omh` resolved, except for `--profile <name>` without
    `--omh-home`: then the store that profile's own
    `plugins.entries.omh.settings.omh_home` names, when it names one. That
    is the store `omh setup` saves the profile's theme in and the profile's
    plugin binds, so the TUI opens with that profile's theme, and its HUD
    and `/omh-model` read the same store. A profile that names none keeps
    the resolved home, which is the store setup gave it.

    So does a profile whose setting OMH cannot read (a shape the standalone
    scan refuses, such as a flow `entries: {omh: ...}`), so its TUI opens
    on the primary's store. That is deliberate: OMH cannot tell which store
    such a setting names and does not guess one, and setup saves no theme
    for that profile (`store_unknown`) rather than put it in the primary's
    store.
    """
    from ..maintenance.hermes_resume import hermes_profile_home
    from ..paths import configured_omh_home

    profile = getattr(args, "tui_profile", None)
    if not profile or paths.omh_home_named:
        return paths.omh_home
    profile_home = hermes_profile_home(paths.hermes_home, profile)
    configured = configured_omh_home(profile_home) if profile_home is not None else None
    return configured or paths.omh_home


def _node_available(paths) -> bool:
    from ..tui.launch import node_available

    return node_available(os.environ, paths.hermes_home)


def _run_terminal(hermes: str, args: argparse.Namespace, paths) -> int:
    """Run the OMH TUI through `hermes --tui`, or Hermes' own terminal when it cannot start.

    Only `FALLBACK_EXIT` together with the sentinel file this launch named
    reopens Hermes' terminal: it is the OMH TUI saying it never got going (it
    has already said why). Any other code, and 75 without the file (Hermes
    exits 75 on paths of its own), is the person's session ending and is
    returned as it is, so a session the person closed is never followed by a
    second terminal.

    While the TUI runs, SIGHUP and SIGTERM end `omh` through SystemExit, so
    the sentinel's directory is removed on the way out. The child is waited
    on with `Popen.wait`, not `subprocess.run`, which kills its child when the
    wait is interrupted: the terminal's SIGHUP reaches Hermes' launcher on its
    own, and a SIGTERM sent to `omh` alone leaves it running, as before.
    """
    from ..tui.launch import (
        exit_on_signals,
        fallback_reason,
        fallback_requested,
        fallback_sentinel,
        omh_import_root,
        omh_tui_environment,
    )

    blocker = _omh_tui_blocker(paths)
    said: list[str] = []
    if not blocker:
        with exit_on_signals(), fallback_sentinel() as sentinel:
            environment = omh_tui_environment(
                os.environ,
                python=sys.executable,
                site=omh_import_root(),
                omh_home=_tui_omh_home(args, paths),
                fallback_sentinel=sentinel,
            )
            child = subprocess.Popen([hermes, *_hermes_passthrough_argv(args, tui=True)], env=environment)
            returncode = int(child.wait())
            fell_back = fallback_requested(returncode, sentinel)
            said = fallback_reason(sentinel) if fell_back else []
        if not fell_back:
            return returncode
        blocker = "the OMH TUI did not start (its reason is above)"
    return _run_hermes_fallback(hermes, args, paths, blocker, said)


def _run_hermes_fallback(
    hermes: str, args: argparse.Namespace, paths, reason: str, said: list[str] | None = None
) -> int:
    """Hermes' own TUI when only the OMH TUI is ruled out, else plain `hermes`.

    Bare `hermes` is stock Hermes and opens whatever `display.interface`
    says, which is the classic REPL unless the person chose otherwise, so
    the TUI has to be asked for: `hermes --tui`, with none of the OMH TUI's
    environment. Only for a reason that is the OMH TUI's own
    (`hermes_tui_fits`), and only where Node is found: Hermes' TUI exits 1
    without it, as the OMH TUI would. Otherwise plain `hermes`, which opens
    what the config chooses. One line on stderr says why the OMH TUI is not
    the one opening. `said` is what the OMH TUI printed about why it did not
    start (`fallback_reason`); Hermes' TUI clears the screen it was on, so it
    is printed again once that terminal closes.
    """
    from ..tui.launch import hermes_tui_fits

    with_tui = hermes_tui_fits(os.environ) and _node_available(paths)
    target = "Hermes' own TUI (`hermes --tui`)" if with_tui else "`hermes`"
    print(f"omh: {reason}; opening {target}.", file=sys.stderr)
    returncode = int(subprocess.run([hermes, *_hermes_passthrough_argv(args, tui=with_tui)]).returncode)
    if said:
        print(f"omh: {target} opened because the OMH TUI did not start; it said:", file=sys.stderr)
        for line in said:
            print(f"  {line}", file=sys.stderr)
    return returncode
