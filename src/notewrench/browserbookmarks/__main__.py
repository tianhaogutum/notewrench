import sys

from notewrench.cli import main

raise SystemExit(main(["browser-bookmarks", *sys.argv[1:]]))
