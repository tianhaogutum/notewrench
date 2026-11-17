"""Top-level ``notewrench`` command: dispatches to one tool per subcommand.

Each tool lives in its own subpackage exposing ``cli.main(argv) -> int``. Tools
are imported lazily, so ``notewrench --help`` stays fast and a tool that is not
shipped in this build reports itself instead of failing with a traceback.

Most commands use only the standard library. A command with an ``extra`` needs the
third-party packages of that optional dependency group; when they are missing, its
``--help``/``--version`` still work and every other invocation prints the one-line
install hint instead of a traceback.
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
    # Optional dependency group (``pip install 'notewrench[<extra>]'``) and the
    # top-level import names it provides; empty for standard-library-only commands.
    extra: str | None = None
    requires: tuple[str, ...] = ()
    # Sub-subcommands that run without the extra (e.g. an environment check that
    # reports the missing packages itself).
    runs_without_extra: tuple[str, ...] = ()


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
    Command("anki-merge", "notewrench.ankimerge.cli",
            "Preview an Anki course update: merge teacher and student note exports",
            "Migrate & sync notes", extra="anki-merge", requires=("csvdiff",)),
    Command("bookmark-migration", "notewrench.bookmarkmigration.cli",
            "Audit a bookmark migration: compare before/after HTML or Karakeep exports",
            "Migrate & sync notes"),
    Command("archive", "notewrench.archive.cli",
            "Rescue cached Wallabag/Karakeep articles as a static HTML archive",
            "Read-later archives", linux_only=True),
    Command("epub", "notewrench.epub.cli",
            "Bundle cached Wallabag/Karakeep articles into a validated EPUB 3",
            "Read-later archives"),
    Command("relink", "notewrench.relink.cli",
            "Point Markdown citations at your existing local web archive copies",
            "Read-later archives", extra="relink",
            requires=("bs4", "idna", "markdown_it", "mdit_py_plugins", "warcio"),
            runs_without_extra=("doctor",)),
    Command("links", "notewrench.linkdedupe.cli",
            "Find duplicate bookmarks across Karakeep and Wallabag exports",
            "Duplicates"),
    Command("browser-bookmarks", "notewrench.browserbookmarks.cli",
            "Review duplicate browser/linkding HTML bookmarks with receipts before import",
            "Duplicates", extra="browser-bookmarks",
            requires=("bs4", "html5lib", "webencodings", "filelock", "ijson")),
    Command("joplin-dupes", "notewrench.joplindupes.cli",
            "Review duplicate Joplin notes; write an approved deduplicated copy",
            "Duplicates"),
    Command("calibre-dupes", "notewrench.calibredupes.cli",
            "Find duplicate books within or across Calibre libraries (read-only)",
            "Duplicates"),
    Command("zotero-dupes", "notewrench.zoterodupes.cli",
            "Review duplicates across Zotero personal and group library exports",
            "Duplicates", extra="zotero-dupes",
            requires=("bib_dedupe", "rdflib", "defusedxml", "rapidfuzz", "pandas", "isbnlib", "jsonschema")),
    Command("citekeys", "notewrench.citekeys.cli",
            "Rewrite Pandoc citekeys in Markdown when CSL-JSON keys change",
            "Citations & attachments"),
    Command("bibtex-keys", "notewrench.bibtexkeys.cli",
            "Preview a BibTeX key change across a notes folder as a reviewable patch",
            "Citations & attachments", extra="bibtex-keys",
            requires=("pybtex", "markdown_it", "mdit_py_plugins", "pylatexenc", "yaml")),
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
    "anki-course-merge": "anki-merge", "archive-relink": "relink", "library-bridge": "zotero-dupes",
    "citekey-audit": "bibtex-keys", "bookmark-receipts": "browser-bookmarks",
    "bookmark-migration-audit": "bookmark-migration", "bookmark-audit": "bookmark-migration",
}


def _present(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def _installed(command: Command) -> bool:
    # Locating the cli module imports only the (light) subpackage __init__, not the tool.
    return _present(command.module.rpartition(".")[0]) and _present(command.module)


def _missing(command: Command) -> list[str]:
    """Top-level modules of the command's optional extra that cannot be imported."""
    return [name for name in command.requires if not _present(name)]


def install_hint(command: Command) -> str:
    return f"pip install '{PROG}[{command.extra}]'"


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
            elif _missing(command):
                note = f"  (needs {PROG}[{command.extra}])"
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


def _needs_extra(command: Command, missing: list[str]) -> int:
    sys.stderr.write(f"{PROG} {command.name}: needs the optional '{command.extra}' dependencies "
                     f"(missing: {', '.join(missing)}). Install them with: {install_hint(command)}\n")
    return 2


def _asks_help(argv: list[str]) -> bool:
    # Help and version output never needs the optional dependencies.
    return any(arg in ("-h", "--help", "--version") for arg in argv)


def run(command: Command, argv: list[str]) -> int:
    if not _installed(command):
        return _not_available(command)
    missing = _missing(command)
    if missing and not _asks_help(argv) and not (argv and argv[0] in command.runs_without_extra):
        return _needs_extra(command, missing)
    try:
        module = importlib.import_module(command.module)
    except ModuleNotFoundError as exc:
        # A subpackage that exists but does not ship its cli module yet.
        if exc.name == command.module:
            return _not_available(command)
        raise
    try:
        result = module.main(argv)
    except ModuleNotFoundError as exc:
        # A dependency of the extra that is importable by name but broken or partial.
        top = (exc.name or "").partition(".")[0]
        if command.extra and top in command.requires:
            return _needs_extra(command, [top])
        raise
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
