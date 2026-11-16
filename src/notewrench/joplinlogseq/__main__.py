import sys

from notewrench.cli import main

raise SystemExit(main(["joplin-logseq", *sys.argv[1:]]))
