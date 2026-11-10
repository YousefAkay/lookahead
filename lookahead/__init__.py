"""Lookahead: local-first cashflow planner."""

__version__ = "0.1.0"

from lookahead.demo_mode import enable_if_demo

enable_if_demo()  # demo mode (LOOKAHEAD_DEMO=1): nothing under data/private can be opened
