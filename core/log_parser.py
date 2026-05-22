"""Parses a SAS .log file and returns a list of structured SASError dataclass records."""

import logging
import re
from dataclasses import dataclass
from pathlib import Path
