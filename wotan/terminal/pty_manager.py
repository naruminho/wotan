"""Compatibility alias: the terminal manager lives in :mod:`wotan.terminal`."""

from . import IS_WINDOWS, TerminalManager, TerminalSession

__all__ = ["IS_WINDOWS", "TerminalManager", "TerminalSession"]
