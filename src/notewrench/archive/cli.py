"""Command-line interface and exclusive bundle writer.

SPDX-License-Identifier: AGPL-3.0-only
"""

import argparse
from contextlib import contextmanager, ExitStack
import errno
import os
from pathlib import Path
import secrets
import stat
import sys

# Load the optional standard-library FFI before acquiring invocation-owned
# descriptors. Some Python loaders retain a descriptor for libffi itself.
try:
    import ctypes
except ImportError:
    ctypes = None

from notewrench import __version__ as NOTEWRENCH_VERSION
from .verify import verify_bundle
from .api import build_api
from .assets import _read, load_image_assets
from .content_assets import load_content_assets
from .core import MAX_INPUT_BYTES, InputError, build, json_text


def read_input(path: Path, *, dir_fd=None):
    """Follow explicit selection symlinks, but read only an opened regular file.

    O_NONBLOCK prevents a selected FIFO (including a replacement at open) from
    waiting for a writer. Check the descriptor itself before reading any bytes.
    This is separate from no-follow confinement of manifest-declared resources.
    """
    try:
        flags = os.O_RDONLY | os.O_NONBLOCK
        if dir_fd is not None:
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags, dir_fd=dir_fd)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise InputError(f"input_path: not a regular file: {path}")
            with os.fdopen(descriptor, "rb", closefd=False) as source:
                return source.read(MAX_INPUT_BYTES + 1)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise InputError(f"input_path: cannot read selected input {path}: {exc}") from exc


@contextmanager
def api_input(path: Path):
    """Bind selected manifest bytes and page reads to one held parent (Linux).

    Resolve explicit static symlinks once for compatibility. Open the resolved
    leaf without following a newly substituted symlink, relative to the parent
    descriptor that will also serve response reads. Never resolve selection
    again after reading. Ancestors and in-place changes are not snapshotted.
    """
    root_fd = None
    try:
        try:
            selected = path.resolve(strict=True)
            root_fd = os.open(selected.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            raw = read_input(Path(selected.name), dir_fd=root_fd)
        except (OSError, ValueError, RuntimeError) as exc:
            raise InputError(f"input_path: cannot acquire selected API manifest {path}: {exc}") from exc
        yield raw, root_fd
    finally:
        if root_fd is not None:
            os.close(root_fd)


@contextmanager
def output_parent(output: Path):
    """Follow static parent selection symlinks once, then hold that directory.

    The output leaf is never resolved: even a dangling symlink is a conflict.
    Ancestor changes during acquisition are not a filesystem snapshot.
    """
    descriptor = None
    try:
        try:
            selected = output.parent.resolve(strict=True)
            descriptor = os.open(selected, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except (OSError, ValueError, RuntimeError) as exc:
            raise InputError(f"output parent cannot be acquired as a directory: {output.parent}: {exc}") from exc
        yield descriptor
    finally:
        if descriptor is not None:
            os.close(descriptor)


def check_output(output: Path, *, parent_fd=None):
    if parent_fd is None:
        with output_parent(output) as descriptor:
            return check_output(output, parent_fd=descriptor)
    try:
        os.stat(output.name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    raise InputError(f"output already exists (never overwritten): {output}")


def _same_directory(parent_fd, name, held):
    try:
        entry = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return (entry.st_dev, entry.st_ino) == (held.st_dev, held.st_ino)


@contextmanager
def _bundle_directory(parent_fd, name):
    """Hold private staging; clean only while it still has its staging name.

    Once renamed, never empty the held directory: publication may have succeeded
    immediately before a Python exception/signal, before any success flag ran.
    A replaced/moved staging entry is conservatively left alone as well.
    """
    os.mkdir(name, mode=0o700, dir_fd=parent_fd)
    descriptor = None
    owned = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    try:
        descriptor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                             dir_fd=parent_fd)
        held = os.fstat(descriptor)
        if (held.st_dev, held.st_ino) != (owned.st_dev, owned.st_ino):
            raise InputError("output staging directory changed during acquisition")
        yield descriptor
    finally:
        try:
            if _same_directory(parent_fd, name, owned):
                if descriptor is not None:
                    held = os.fstat(descriptor)
                    if (held.st_dev, held.st_ino) == (owned.st_dev, owned.st_ino):
                        for child in os.listdir(descriptor):
                            os.unlink(child, dir_fd=descriptor)
                if _same_directory(parent_fd, name, owned):
                    os.rmdir(name, dir_fd=parent_fd)
        finally:
            if descriptor is not None:
                os.close(descriptor)


def _publish_directory(parent_fd, staging_name, staging_fd, output_name):
    """Linux renameat2(RENAME_NOREPLACE), with no overwriting fallback.

    Relative source/destination names use the same held parent. Validate that
    the private source still names the held staging object before the syscall.
    This is not exclusion of hostile same-user edits to private staging names.
    """
    unsupported = "atomic output publication requires Linux libc renameat2(RENAME_NOREPLACE) and filesystem support"
    if sys.platform != "linux" or ctypes is None:
        raise InputError(unsupported)
    try:
        renameat2 = ctypes.CDLL(None, use_errno=True).renameat2
    except (ImportError, AttributeError, OSError) as exc:
        raise InputError(unsupported) from exc
    renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    renameat2.restype = ctypes.c_int
    if not _same_directory(parent_fd, staging_name, os.fstat(staging_fd)):
        raise InputError("output staging directory changed before publication")
    result = renameat2(parent_fd, os.fsencode(staging_name), parent_fd, os.fsencode(output_name), 1)
    if result != 0:
        code = ctypes.get_errno()
        if code in {errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP}:
            raise InputError(unsupported + f": {os.strerror(code)}")
        raise OSError(code, os.strerror(code), output_name)


def write_bundle(output: Path, files: dict[str, str | bytes], *, parent_fd=None):
    """Stage and atomically publish relative to one held output parent (Linux).

    Existing targets are never replaced, including targets appearing during
    staging. A process interruption leaves the target absent or complete;
    no fsync/power-loss durability is promised.
    """
    if parent_fd is None:
        with output_parent(output) as descriptor:
            return write_bundle(output, files, parent_fd=descriptor)
    check_output(output, parent_fd=parent_fd)
    staging_name = ".bagkeep-" + secrets.token_hex(16)
    with _bundle_directory(parent_fd, staging_name) as staging_fd:
        for name, content in files.items():
            # Internal names are generated from constants/digests only.
            if Path(name).name != name or name in {".", ".."}:
                raise InputError("internal output filename is not a basename")
            descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o666, dir_fd=staging_fd)
            try:
                with os.fdopen(descriptor, "wb", closefd=False) as target:
                    target.write(content if isinstance(content, bytes) else content.encode("utf-8"))
            finally:
                os.close(descriptor)
        _publish_directory(parent_fd, staging_name, staging_fd, output.name)


def parser():
    cli = argparse.ArgumentParser(prog="notewrench archive", description=(
        "Rescue cached Wallabag/Karakeep article bodies into a static offline HTML archive, "
        "convert Karakeep exports to Wallabag v2 JSON, and verify published bundles. Offline; no network."))
    cli.add_argument("--version", action="version", version=f"%(prog)s {NOTEWRENCH_VERSION}")
    sub = cli.add_subparsers(dest="command", required=True)
    for name, description in (("rescue", "Wallabag v2 JSON to safe static HTML archive"),
                              ("k2w", "Karakeep native JSON to Wallabag v2 JSON bundle"),
                              ("api-import", "Saved API pagination manifest to offline archive and Wallabag JSON")):
        command = sub.add_parser(name, help=description, description=description)
        command.add_argument("input", type=Path, help="local UTF-8 JSON export or API manifest (maximum 64 MiB)")
        command.add_argument("-o", "--output", required=True, type=Path, help="new bundle directory; parent must exist; never overwritten")
        command.add_argument("--dry-run", action="store_true", help="validate and print JSON plan without writing any files")
        command.add_argument("--strict", action="store_true", help="exit 3 without writing if manifest contains loss issues")
        if name in {"rescue", "api-import"}:
            command.add_argument("--assets", type=Path, help="explicit local static PNG/baseline JPEG manifest (64 MiB aggregate); no fetching")
        if name == "api-import":
            command.add_argument("--content-assets", type=Path, help="scoped local Karakeep saved HTML manifest (64 MiB aggregate); no fetching")
            command.add_argument("--require-page-hashes", action="store_true", help="require a matching SHA-256 pin on every saved API page (default: pins optional)")
    verify = sub.add_parser("verify", help="read-only published bundle size/SHA-256 consistency check",
                            description="Re-check a published flat bundle: every declared file size and SHA-256 against its manifest (Linux). Read-only; needs no original inputs.")
    verify.add_argument("bundle", type=Path, help="existing flat bundle directory; no original inputs required")
    return cli


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "verify":
            result = verify_bundle(args.bundle)
            print(json_text(result), end="")
            return 0 if result["status"] == "valid" else 2
        with ExitStack() as selection:
            if args.command == "api-import":
                raw, root_fd = selection.enter_context(api_input(args.input))
            else:
                raw = read_input(args.input)
            image_assets = load_image_assets(args.assets) if getattr(args, "assets", None) else None
            if args.command == "api-import":
                content_assets = load_content_assets(args.content_assets) if args.content_assets else None

                def load_page(name, remaining):
                    try:
                        data = _read(root_fd, name, remaining)
                    except (InputError, OSError) as exc:
                        detail = str(exc).removeprefix('local_asset_path: ')
                        raise InputError(f"local_page_path: {detail}") from exc
                    if data is None:
                        raise InputError("local_page_path: missing response file")
                    return data

                files, manifest = build_api(raw, load_page, image_assets, content_assets,
                                            require_page_hashes=args.require_page_hashes)
            else:
                files, manifest = build(args.command, raw, image_assets)
        output = args.output.absolute()
        with output_parent(output) as parent_fd:
            check_output(output, parent_fd=parent_fd)
            losses = sum(i["severity"] == "loss" for i in manifest["issues"])
            rejected = args.strict and losses > 0
            result = {"status": "strict_rejected" if rejected else ("planned" if args.dry_run else "written"),
                      "output": str(output), "dry_run": args.dry_run,
                      "would_write": sorted(files), "loss_count": losses, "manifest": manifest}
            if not rejected and not args.dry_run:
                write_bundle(output, files, parent_fd=parent_fd)
            print(json_text(result), end="")
        return 3 if rejected else 0
    except (OSError, ValueError, RecursionError) as exc:
        print(f"notewrench archive: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("notewrench archive: interrupted", file=sys.stderr)
        return 130
