"""Loads environment variables from .env and exposes them as a typed Config dataclass."""

import logging
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    openai_api_key: str
    log_path: str = ""
    egp_path: str = ""
    dry_run: bool = False
    parse_only: bool = False
    log_level: str = "INFO"


def load_config() -> Config:
    """Read required environment variables and return a populated Config.

    Raises RuntimeError if OPENAI_API_KEY is absent.
    log_path and egp_path are intentionally left empty here — callers set
    them from CLI arguments after calling load_config().
    """
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set. Add it to your .env file.")
    log_level = os.environ.get("LOG_LEVEL", "INFO")
    return Config(openai_api_key=api_key, log_level=log_level)


def configure_logging(level: str) -> None:
    """Configure the root logger with a consistent timestamp format."""
    logging.basicConfig(
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        level=level,
    )
