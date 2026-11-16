import sys

from notewrench.cli import main

raise SystemExit(main(["links", *sys.argv[1:]]))
