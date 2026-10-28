#!/usr/bin/env python3
"""Pull one zone or the global gdnsd configuration through Juju."""

import sys

from gdnsd_cli import main

if __name__ == "__main__":
    try:
        sys.exit(main("pull"))
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Pull failed: {exc}", file=sys.stderr)
        sys.exit(1)
