import sys

from notewrench.cli import main

raise SystemExit(main(["wallabag-notes", *sys.argv[1:]]))
