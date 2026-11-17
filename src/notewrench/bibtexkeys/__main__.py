import sys

from notewrench.cli import main

raise SystemExit(main(["bibtex-keys", *sys.argv[1:]]))
