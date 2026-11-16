# SPDX-License-Identifier: AGPL-3.0-only
"""Original, invented Joplin-format exports; no upstream fixtures or note data."""
from __future__ import annotations

import io
from pathlib import Path
import sys
import tarfile

# The installed command, runnable from any working directory.
COMMAND = (sys.executable, "-m", "notewrench", "joplin-dupes")


def uid(number: int) -> str:
    return f"{number:032x}"


def record(item_id: str, kind: int, title: str | None = None,
           body: str | None = None, **fields) -> bytes:
    sections = []
    if title is not None:
        sections.append(title)
    if body:
        sections.append(body)
    props = {"id": item_id, **fields, "type_": str(kind)}
    sections.append("\n".join(f"{k}: {v}" for k, v in props.items()))
    return "\n\n".join(sections).encode("utf-8")


def write_raw(path: Path, files: dict[str, bytes]) -> Path:
    path.mkdir(parents=True)
    for name, data in files.items():
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return path


def write_jex(path: Path, files: dict[str, bytes]) -> Path:
    with tarfile.open(path, "w", format=tarfile.USTAR_FORMAT) as archive:
        for name, data in sorted(files.items()):
            info = tarfile.TarInfo(name)
            info.size, info.mtime, info.mode = len(data), 0, 0o600
            archive.addfile(info, io.BytesIO(data))
    return path


def example_files() -> dict[str, bytes]:
    """Two copies with re-ID'd attachments; a same-title different-body note."""
    f1, f2, tag = uid(100), uid(101), uid(200)
    files = {
        f1 + ".md": record(f1, 2, "Field notes", parent_id=""),
        f2 + ".md": record(f2, 2, "Field notes (imported)", parent_id=""),
        tag + ".md": record(tag, 5, "synthetic"),
    }
    for i in (1, 2):
        note_id, resource_id, relation = uid(i), uid(300 + i), uid(400 + i)
        body = f"Café observations\n\n![sample](:/{resource_id})\n\ncode: this is body text"
        if i == 2:
            body = body.replace("é", "e\u0301").replace("\n", "\r\n")
        files[note_id + ".md"] = record(
            note_id, 1, "Observation", body, parent_id=f1 if i == 1 else f2,
            created_time=f"2026-01-0{i}T12:00:00.000Z", updated_time="2026-01-03T12:00:00.000Z",
            is_todo=0, todo_due=0, todo_completed=0, is_conflict=0, markup_language=1,
            encryption_applied=0, synthetic_extra="retained: yes")
        blob = b"Synthetic attachment. This is not a real user's file.\n"
        files[resource_id + ".md"] = record(resource_id, 4, "sample.txt", mime="text/plain",
                                             file_extension="txt", size=len(blob), encryption_blob_encrypted=0)
        files[f"resources/{resource_id}.txt"] = blob
        files[relation + ".md"] = record(relation, 6, note_id=note_id, tag_id=tag)
    files[uid(3) + ".md"] = record(uid(3), 1, "Observation", "A different observation.",
                                    parent_id=f1, is_todo=0, todo_completed=0, is_conflict=0)
    return files


def linked_files(offset: int = 0) -> dict[str, bytes]:
    """A three-note acyclic set; offset remaps every note/resource ID."""
    index, middle, detail, resource = [uid(offset + n) for n in (1, 2, 3, 101)]
    blob = b"Synthetic linked-note attachment.\n"
    return {
        index + ".md": record(index, 1, "Index", f"Read [middle](:/{middle}) twice: [middle](:/{middle})."),
        middle + ".md": record(middle, 1, "Middle", f"Read [detail](:/{detail}#section)."),
        detail + ".md": record(detail, 1, "Detail", f"Café detail\n\n[data](:/{resource})", is_todo=1),
        resource + ".md": record(resource, 4, "data.txt", mime="text/plain",
                                 file_extension="txt", size=len(blob)),
        f"resources/{resource}.txt": blob,
    }


def benchmark_files(families: int) -> tuple[dict[str, bytes], set[tuple[str, str]]]:
    """Each family: 3 true copies, changed bytes, changed TODO, changed body."""
    folder = uid(900000)
    files = {folder + ".md": record(folder, 2, "Benchmark", parent_id="")}
    truth = set()
    for family in range(families):
        note_ids = [uid(1000 + family * 10 + i) for i in range(6)]
        for a in range(3):
            for b in range(a + 1, 3):
                truth.add(tuple(sorted((note_ids[a], note_ids[b]))))
        for i, note_id in enumerate(note_ids):
            resource_id = uid(100000 + family * 10 + i)
            blob = (f"synthetic attachment family {family}\n" * 40).encode()
            if i == 3:
                blob += b"changed bytes\n"
            title = f"Topic {family}" if i != 2 else f"Renamed topic {family}"
            body = f"Family {family} text\n\n[attachment](:/{resource_id})"
            if i == 5:
                body += "\nDifferent content, despite equal title."
            files[note_id + ".md"] = record(
                note_id, 1, title, body, parent_id=folder, is_todo=1,
                todo_due=0, todo_completed=1000 if i == 4 else 0,
                is_conflict=0, markup_language=1)
            files[resource_id + ".md"] = record(resource_id, 4, "data.txt", mime="text/plain",
                                                 file_extension="txt", size=len(blob))
            files[f"resources/{resource_id}.txt"] = blob
    return files, truth
