"""Top-level ``notewrench`` command: dispatches to one tool per subcommand.

Each tool lives in its own subpackage exposing ``cli.main(argv) -> int``. Tools
are imported lazily, so ``notewrench --help`` stays fast and a tool that is not
shipped in this build reports itself instead of failing with a traceback.
"""
from __future__ import annotations

from dataclasses import dataclass
import difflib
import importlib
import importlib.util
import sys

from . import __version__

PROG = "notewrench"
REPO_URL = "https://github.com/tianhaogutum/notewrench"


@dataclass(frozen=True)
class Command:
    name: str
    module: str
    summary: str
    group: str
    # Needs Linux-only kernel interfaces for its main mode (still runs elsewhere,
    # where the affected operations report a clear error instead).
    linux_only: bool = False


GROUPS = ("Check", "Migrate & sync notes", "Read-later archives", "Duplicates", "Citations & attachments")

COMMANDS: tuple[Command, ...] = (
    Command("doctor", "notewrench.doctor.cli",
            "Read-only health check of your exports and libraries, with the fix to run",
            "Check"),
    Command("zotero-logseq", "notewrench.zoterologseq.cli",
            "Sync a Zotero CSL-JSON export into Logseq pages, keeping your notes intact",
            "Migrate & sync notes"),
    Command("joplin-logseq", "notewrench.joplinlogseq.cli",
            "Convert a Joplin JEX export into a new Logseq graph with a loss report",
            "Migrate & sync notes"),
    Command("wallabag-notes", "notewrench.wallabagnotes.cli",
            "Export Wallabag highlights and annotations to Markdown, Logseq blocks and CSV",
            "Migrate & sync notes"),
    Command("archive", "notewrench.archive.cli",
            "Rescue cached Wallabag/Karakeep articles as a static HTML archive",
            "Read-later archives", linux_only=True),
    Command("epub", "notewrench.epub.cli",
            "Bundle cached Wallabag/Karakeep articles into a validated EPUB 3",
            "Read-later archives"),
    Command("links", "notewrench.linkdedupe.cli",
            "Find duplicate bookmarks across Karakeep and Wallabag exports",
            "Duplicates"),
    Command("joplin-dupes", "notewrench.joplindupes.cli",
            "Review duplicate Joplin notes; write an approved deduplicated copy",
            "Duplicates"),
    Command("calibre-dupes", "notewrench.calibredupes.cli",
            "Find duplicate books within or across Calibre libraries (read-only)",
            "Duplicates"),
    Command("citekeys", "notewrench.citekeys.cli",
            "Rewrite Pandoc citekeys in Markdown when CSL-JSON keys change",
            "Citations & attachments"),
    Command("zotero-attachments", "notewrench.zoteroattachments.cli",
            "Audit Zotero storage for duplicate, missing and orphaned attachments",
            "Citations & attachments", linux_only=True),
)
BY_NAME = {command.name: command for command in COMMANDS}

# Former standalone tool names, used only to point users at the new subcommand.
# (calibre-dupes kept its name.)
LEGACY = {
    "zotlog-sync": "zotero-logseq", "jex2logseq": "joplin-logseq", "bagnotes": "wallabag-notes",
    "bagkeep": "archive", "readbundle": "epub", "linkdedupe": "links", "joplin-dedupe": "joplin-dupes",
    "citekey-rekey": "citekeys", "zotero-attach-audit": "zotero-attachments",
}


def _present(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def _installed(command: Command) -> bool:
    # Locating the cli module imports only the (light) subpackage __init__, not the tool.
    return _present(command.module.rpartition(".")[0]) and _present(command.module)


def _on_linux() -> bool:
    return sys.platform.startswith("linux")


def overview() -> str:
    width = max(len(command.name) for command in COMMANDS) + 2
    lines = [
        f"{PROG} {__version__} - offline tools for personal knowledge bases",
        "",
        f"usage: {PROG} <command> [args...]",
        f"       {PROG} <command> --help",
    ]
    for group in GROUPS:
        lines += ["", f"{group}:"]
        for command in COMMANDS:
            if command.group != group:
                continue
            note = ""
            if not _installed(command):
                note = "  (coming soon)"
            elif command.linux_only and not _on_linux():
                note = "  (Linux only)"
            lines.append(f"  {command.name.ljust(width)}{command.summary}{note}")
    lines += [
        "",
        "options:",
        "  -h, --help     show this overview",
        "  -V, --version  show the version and exit",
        "",
        f"Docs and issues: {REPO_URL}",
    ]
    return "\n".join(lines) + "\n"


def _unknown(name: str) -> int:
    message = f"{PROG}: unknown command {name!r}"
    if name in LEGACY:
        message += f"; {name} is now '{PROG} {LEGACY[name]}'"
    else:
        match = difflib.get_close_matches(name, [*BY_NAME, *LEGACY], n=1, cutoff=0.6)
        if match:
            suggestion = LEGACY.get(match[0], match[0])
            message += f"; did you mean '{suggestion}'?"
    sys.stderr.write(message + f"\nRun '{PROG} --help' to list commands.\n")
    return 2


def _not_available(command: Command) -> int:
    sys.stderr.write(f"{PROG}: command {command.name!r} is not available in "
                     f"{PROG} {__version__} yet; see {REPO_URL}\n")
    return 2


def run(command: Command, argv: list[str]) -> int:
    if not _installed(command):
        return _not_available(command)
    try:
        module = importlib.import_module(command.module)
    except ModuleNotFoundError as exc:
        # A subpackage that exists but does not ship its cli module yet.
        if exc.name == command.module:
            return _not_available(command)
        raise
    result = module.main(argv)
    return 0 if result is None else result


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):
        sys.stdout.write(overview())
        return 0
    first, rest = args[0], args[1:]
    if first in ("-V", "--version"):
        sys.stdout.write(f"{PROG} {__version__}\n")
        return 0
    if first == "help":
        if not rest:
            sys.stdout.write(overview())
            return 0
        first, rest = rest[0], ["--help"]
    if first.startswith("-"):
        sys.stderr.write(f"{PROG}: unknown option {first!r}\nRun '{PROG} --help' for usage.\n")
        return 2
    command = BY_NAME.get(first)
    if command is None:
        return _unknown(first)
    return run(command, rest)


if __name__ == "__main__":
    raise SystemExit(main())
