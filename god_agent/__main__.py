"""Direct package execution entrypoint (`python3 -m god_agent`)."""
from __future__ import annotations

import sys
from god_agent.cli import main

if __name__ == "__main__":
    sys.exit(main())
