import sys

from notewrench.cli import main

raise SystemExit(main(["joplin-dupes", *sys.argv[1:]]))
