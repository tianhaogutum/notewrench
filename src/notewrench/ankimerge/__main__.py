import sys

from notewrench.cli import main

raise SystemExit(main(["anki-merge", *sys.argv[1:]]))
