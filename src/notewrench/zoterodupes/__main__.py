import sys

from notewrench.cli import main

raise SystemExit(main(["zotero-dupes", *sys.argv[1:]]))
