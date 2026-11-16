"""Load the synthetic fixture generator shipped in examples/zotero-attachments/."""
import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / 'examples' / 'zotero-attachments' / 'make_fixture.py'
_SPEC = importlib.util.spec_from_file_location('_zotero_attachments_make_fixture', _PATH)
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)

create_fixture = _MODULE.create_fixture
