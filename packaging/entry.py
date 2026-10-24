"""Entry point used by PyInstaller. Kept outside the package so the frozen app starts cleanly."""

import multiprocessing
import os
import sys

from decision_agent.desktop.cli import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    # The windowed exe has no console, so sys.stdout and sys.stderr are None. Libraries that
    # write to them would fail; send that output nowhere (the log file keeps what matters).
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    sys.exit(main())
