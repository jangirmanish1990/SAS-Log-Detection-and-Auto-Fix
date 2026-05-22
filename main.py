"""CLI entry point — parses arguments, loads config, and runs the fixer orchestrator."""

import argparse
import logging
import sys
from pathlib import Path

from config import Config
from agents.orchestrator import build_graph
