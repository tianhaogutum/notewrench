"""Load the synthetic library writer and benchmark from examples/calibre-dupes/.

They are runnable scripts, not importable packages, so load them by path.
"""
import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "examples" / "calibre-dupes"


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


synthetic = _load("_notewrench_calibredupes_synthetic", EXAMPLES / "synthetic.py")
create_library = synthetic.create_library
sample_libraries = synthetic.sample_libraries
verification_libraries = synthetic.verification_libraries
decision_libraries = synthetic.decision_libraries
donor_libraries = synthetic.donor_libraries


def benchmark():
    return _load("_notewrench_calibredupes_benchmark", EXAMPLES / "benchmark.py")
