import sys

from notewrench.cli import main

raise SystemExit(main(["zotero-attachments", *sys.argv[1:]]))
