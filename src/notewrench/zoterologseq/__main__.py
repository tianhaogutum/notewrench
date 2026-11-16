import sys

from notewrench.cli import main

raise SystemExit(main(["zotero-logseq", *sys.argv[1:]]))
