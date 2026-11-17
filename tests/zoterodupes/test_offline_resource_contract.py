import concurrent.futures
import hashlib
import json
import multiprocessing
import shutil
import socket

from notewrench.zoterodupes.cli import main
from .paths import UPSTREAM


def test_real_multi_library_workflow_never_opens_network_or_worker_pool(tmp_path, monkeypatch):
    """108 real records cross upstream's small-data threshold; CPU=1 must hold."""
    def forbidden(*args, **kwargs):
        raise AssertionError("Network access or a worker pool was attempted")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(multiprocessing, "Pool", forbidden)
    monkeypatch.setattr(concurrent.futures, "ProcessPoolExecutor", forbidden)
    source = UPSTREAM / "bbt-collected-notes.json"
    paths, hashes, arguments = [], [], ["compare"]
    for label in ["personal", "group-a", "group-b"]:
        path = tmp_path / (label + ".json")
        shutil.copy2(source, path)
        paths.append(path)
        hashes.append(hashlib.sha256(path.read_bytes()).hexdigest())
        arguments.extend(["--library", f"{label}={path}"])
    review_dir = tmp_path / "review"
    arguments.extend(["--output", str(review_dir)])
    assert main(arguments) == 0
    review = json.loads((review_dir / "review.json").read_text())
    assert len(review["records"]) == 108
    assert review["matching"]["cpu"] == 1
    assert len(review["pairs"]) >= 108
    assert main(["finalize", "--review", str(review_dir / "review.json"), "--decisions", str(review_dir / "decisions.json"), "--output", str(tmp_path / "confirmed")]) == 0
    mapping = json.loads((tmp_path / "confirmed/correspondences.json").read_text())
    assert mapping["correspondences"] == []
    assert [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths] == hashes

