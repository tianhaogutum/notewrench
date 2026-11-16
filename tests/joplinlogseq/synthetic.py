"""Synthetic JEX builders shared by the tests.

The single source is ``examples/joplin-logseq/make_synthetic.py`` (which also
regenerates ``examples/joplin-logseq/synthetic.jex``); it is loaded by path so
``examples/`` does not have to be an importable package.
"""
import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / 'examples' / 'joplin-logseq' / 'make_synthetic.py'
_SPEC = importlib.util.spec_from_file_location('_notewrench_joplinlogseq_make_synthetic', _PATH)
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)

CHILD, FOLDER, MISSING = _MODULE.CHILD, _MODULE.FOLDER, _MODULE.MISSING
NOTE_A, NOTE_B, NOTE_C = _MODULE.NOTE_A, _MODULE.NOTE_B, _MODULE.NOTE_C
RELATION, RESOURCE, TAG = _MODULE.RELATION, _MODULE.RESOURCE, _MODULE.TAG
png_bytes, record, sample_files, write_jex = _MODULE.png_bytes, _MODULE.record, _MODULE.sample_files, _MODULE.write_jex
