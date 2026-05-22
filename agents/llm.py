"""Single instantiation point for the OpenAI client.

This is the only file in the project that calls openai.OpenAI().
All agent nodes must import get_llm_client() from here.
Never instantiate openai.OpenAI() anywhere else — see CLAUDE.md rule #1.
"""

import logging
import os

import openai
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

DEFAULT_MODEL: str = "gpt-4o"
FAST_MODEL: str = "gpt-4o-mini"

_api_key: str = os.environ.get("OPENAI_API_KEY", "")
if not _api_key:
    raise RuntimeError(
        "OPENAI_API_KEY is not set. Add it to your .env file."
    )

_client: openai.OpenAI = openai.OpenAI(api_key=_api_key)


def get_llm_client() -> openai.OpenAI:
    """Return the shared OpenAI client instance."""
    return _client
