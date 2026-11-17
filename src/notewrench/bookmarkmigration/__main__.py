import sys

from notewrench.cli import main

raise SystemExit(main(["bookmark-migration", *sys.argv[1:]]))
