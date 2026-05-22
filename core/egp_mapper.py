"""Opens a .egp ZIP archive, locates code nodes, and maps SASError records to line offsets."""

import logging
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path
