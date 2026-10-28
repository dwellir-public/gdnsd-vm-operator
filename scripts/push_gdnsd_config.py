#!/usr/bin/env python3
"""Validate or push one gdnsd configuration file through Juju."""

import sys

from gdnsd_cli import main

if __name__ == "__main__":
    try:
        sys.exit(main("push"))
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Push failed: {exc}", file=sys.stderr)
        sys.exit(1)
