import sys

from notewrench.cli import main

raise SystemExit(main(["calibre-dupes", *sys.argv[1:]]))
