import sys

from notewrench.cli import main

raise SystemExit(main(["archive", *sys.argv[1:]]))
