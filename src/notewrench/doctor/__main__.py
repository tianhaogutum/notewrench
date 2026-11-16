import sys

from notewrench.cli import main

raise SystemExit(main(["doctor", *sys.argv[1:]]))
