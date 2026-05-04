#!/usr/bin/env python3
import sys

# Ensure the user runs this with Python 3.12+ so `requirements.txt` installs correctly
if sys.version_info < (3, 12):
    sys.stderr.write(
        "Snowsky Echo Mini Toolbox requires Python 3.12 or newer.\n"
        "Please install Python 3.12 and re-run inside a 3.12 virtual environment.\n"
    )
    raise SystemExit(2)

from src.app import main


if __name__ == "__main__":
    raise SystemExit(main())
