#!/usr/bin/env python3
"""Development wrapper for the installable ``clip.play`` command.

EXAMPLE tool: the Clip has no echo cancellation — use headphones.
"""

from _bootstrap import SDK_ROOT  # noqa: F401
from clip.tools.play import main


if __name__ == "__main__":
    raise SystemExit(main())
