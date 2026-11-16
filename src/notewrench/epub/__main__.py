import sys

from notewrench.cli import main

raise SystemExit(main(["epub", *sys.argv[1:]]))
