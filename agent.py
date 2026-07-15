#!/usr/bin/env python3
"""Entry point for the Autonomous RTL Generation and Verification agent.

Usage:
    python agent.py
"""

import sys

from rtl_agent.terminal_ui import main

if __name__ == "__main__":
    sys.exit(main())
