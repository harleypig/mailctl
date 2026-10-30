"""Allow ``python -m mailctl.api`` alongside the installed console script."""

import sys

from . import main

if __name__ == "__main__":
    sys.exit(main())
