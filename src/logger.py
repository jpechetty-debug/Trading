# src/logger.py
"""
Centralized Logging for Indian Stock AI V6.5.
Replaces all raw print() calls with structured, leveled logging.
"""
import logging
import sys

# Emoji-to-level mapping for consistent output
_EMOJI_MAP = {
    "DEBUG": "🔍",
    "INFO": "📋",
    "WARNING": "⚠️",
    "ERROR": "❌",
    "CRITICAL": "⛔",
}


class EmojiFormatter(logging.Formatter):
    """Custom formatter that preserves the emoji-style output the system uses."""

    def format(self, record):
        emoji = _EMOJI_MAP.get(record.levelname, "")
        record.emoji = emoji
        return super().format(record)


def get_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """
    Returns a named logger with console output.
    
    Usage:
        from src.logger import get_logger
        log = get_logger(__name__)
        log.info("Data loaded for %s", ticker)
        log.warning("Insufficient liquidity for %s", ticker)
        log.error("Data pipeline failure: %s", error)
    """
    logger = logging.getLogger(name)

    # Avoid adding duplicate handlers on repeated calls
    if logger.handlers:
        return logger

    logger.setLevel(level)

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)

    fmt = EmojiFormatter(
        "%(emoji)s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    handler.setFormatter(fmt)
    logger.addHandler(handler)

    # Don't propagate to root logger
    logger.propagate = False

    return logger
