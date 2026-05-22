"""Loads environment variables from .env and exposes them as a typed Config dataclass."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv
