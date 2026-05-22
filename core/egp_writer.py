"""Writes patched code nodes back into a new _fixed.egp ZIP — never touches the original."""

import logging
import shutil
import zipfile
from pathlib import Path
