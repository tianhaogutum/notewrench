import sys

from notewrench.cli import main

raise SystemExit(main(["relink", *sys.argv[1:]]))
