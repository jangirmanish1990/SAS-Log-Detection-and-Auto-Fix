"""Opens a .egp ZIP archive, locates code nodes, and maps SASError records to line offsets."""

import logging
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field

from core.log_parser import SASError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Strips XML namespace braces: "{urn:schemas-something}Tag" -> "Tag"
_NS_STRIP_RE = re.compile(r"\{[^}]+\}")

# Element tag names (namespace-stripped) that SAS EG uses to store code
_CODE_TAGS = frozenset(
    {"Code", "SourceCode", "SASCode", "CodeTemplate", "EmbeddedSASCode"}
)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ErrorMapping:
    error: SASError
    node_id: str
    node_name: str
    xml_file: str
    local_line: int  # 1-based line number within the code node
    code_context: str  # ±15 lines around the error, >>> marker on error line
    node_code: str  # complete raw code text of the node, no markers or annotation


@dataclass
class CodeNode:
    node_id: str
    node_name: str
    xml_file: str
    element_tag: str
    code: str
    start_line: int  # cumulative start in the submitted code block
    end_line: int  # cumulative end in the submitted code block

    @property
    def line_count(self) -> int:
        return self.end_line - self.start_line + 1

    def get_context(self, local_line: int, window: int = 15) -> str:
        """Return annotated context lines within ±window of local_line (1-based)."""
        code_lines = self.code.splitlines()
        first = max(0, local_line - 1 - window)
        last = min(len(code_lines), local_line + window)
        parts: list[str] = []
        for idx in range(first, last):
            n = idx + 1  # 1-based
            line = code_lines[idx]
            prefix = ">>>" if n == local_line else "   "
            parts.append(f"{prefix} {n:4d} | {line}")
        return "\n".join(parts)


@dataclass
class EGPLineMap:
    egp_path: str
    nodes: list[CodeNode] = field(default_factory=list)
    total_lines: int = 0

    def find_node(self, global_line: int) -> tuple[CodeNode | None, int]:
        """Return (node, local_line) for the node that owns global_line.

        local_line is 1-based within the node's code.
        Returns (None, -1) when global_line falls outside all nodes.
        """
        for node in self.nodes:
            if node.start_line <= global_line <= node.end_line:
                local_line = global_line - node.start_line + 1
                return node, local_line
        return None, -1

    def summary(self) -> str:
        """Return a one-line-per-node summary of the line map."""
        lines: list[str] = [f"EGP line map: {self.egp_path}"]
        for node in self.nodes:
            lines.append(
                f"  [{node.start_line:>4}-{node.end_line:<4}]  "
                f"{node.node_name}  ({node.xml_file})"
            )
        lines.append(f"  Total submitted lines: {self.total_lines}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _strip_ns(tag: str) -> str:
    """Remove the XML namespace prefix from an element tag."""
    return _NS_STRIP_RE.sub("", tag)


def _register_namespaces(tree_bytes: bytes) -> None:
    """Pre-register all namespaces found in an XML document to avoid ns0/ns1 mangling."""
    for event, elem in ET.iterparse(
        __import__("io").BytesIO(tree_bytes), events=["start-ns"]
    ):
        prefix, uri = elem
        try:
            ET.register_namespace(str(prefix), str(uri))
        except ValueError:
            pass


def _extract_nodes_from_xml(
    xml_bytes: bytes,
    xml_filename: str,
) -> list[tuple[str, str, str, str]]:
    """Parse one XML file and return (node_id, node_name, element_tag, code) tuples."""
    _register_namespaces(xml_bytes)

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        logger.warning("Skipping %s — XML parse error: %s", xml_filename, exc)
        return []

    # Build parent map once per XML file so the per-element ancestor walk is O(depth)
    parent_map: dict[ET.Element, ET.Element] = {
        child: parent for parent in root.iter() for child in parent
    }
    results: list[tuple[str, str, str, str]] = []

    for elem in root.iter():
        bare_tag = _strip_ns(elem.tag)
        if bare_tag not in _CODE_TAGS:
            continue

        code_text = elem.text or ""
        if not code_text.strip():
            continue

        # Use the Code element's own id first; only walk up to parents as fallback.
        node_id = elem.get("id") or elem.get("Id") or ""
        node_name = bare_tag
        ancestor: ET.Element | None = elem
        while ancestor is not None:
            if not node_id:
                candidate_id = ancestor.get("id") or ancestor.get("Id") or ""
                if candidate_id:
                    node_id = candidate_id
            candidate_name = (
                ancestor.get("name") or ancestor.get("Name") or ancestor.get("label")
            )
            if candidate_name:
                node_name = candidate_name
            ancestor = parent_map.get(ancestor)

        results.append((node_id, node_name, bare_tag, code_text))

    return results


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_line_map(egp_path: str) -> EGPLineMap:
    """Open a .egp ZIP archive and build a cumulative line map of all code nodes."""
    line_map = EGPLineMap(egp_path=egp_path)
    cursor = 1  # next available global line number

    try:
        zf = zipfile.ZipFile(egp_path, "r")
    except (FileNotFoundError, zipfile.BadZipFile) as exc:
        logger.error("Cannot open EGP archive %s: %s", egp_path, exc)
        raise

    with zf:
        xml_names = sorted(
            name for name in zf.namelist() if name.lower().endswith(".xml")
        )
        logger.debug("Found %d XML file(s) in %s", len(xml_names), egp_path)

        for xml_name in xml_names:
            xml_bytes = zf.read(xml_name)
            raw_nodes = _extract_nodes_from_xml(xml_bytes, xml_name)

            for node_id, node_name, element_tag, code in raw_nodes:
                line_count = len(code.splitlines()) or 1
                node = CodeNode(
                    node_id=node_id,
                    node_name=node_name,
                    xml_file=xml_name,
                    element_tag=element_tag,
                    code=code,
                    start_line=cursor,
                    end_line=cursor + line_count - 1,
                )
                line_map.nodes.append(node)
                logger.debug(
                    "Registered node %r [%d-%d] from %s",
                    node_name,
                    node.start_line,
                    node.end_line,
                    xml_name,
                )
                cursor += line_count

    line_map.total_lines = cursor - 1
    logger.info(
        "build_line_map: %d node(s), %d total line(s) from %s",
        len(line_map.nodes),
        line_map.total_lines,
        egp_path,
    )
    return line_map


def map_errors(
    errors: list[SASError],
    line_map: EGPLineMap,
) -> list[ErrorMapping]:
    """Resolve each SASError to the code node and local line that produced it."""
    mappings: list[ErrorMapping] = []

    for error in errors:
        if error.log_line_number is None:
            logger.warning(
                "Skipping %s error (no line number): %s",
                error.severity,
                error.message[:80],
            )
            continue

        node, local_line = line_map.find_node(error.log_line_number)

        if node is None:
            logger.warning(
                "Line %d not found in EGP line map — skipping %s: %s",
                error.log_line_number,
                error.severity,
                error.message[:80],
            )
            continue

        context = node.get_context(local_line)
        mappings.append(
            ErrorMapping(
                error=error,
                node_id=node.node_id,
                node_name=node.node_name,
                xml_file=node.xml_file,
                local_line=local_line,
                code_context=context,
                node_code=node.code,
            )
        )
        logger.debug(
            "Mapped %s at global line %d → node %r local line %d",
            error.severity,
            error.log_line_number,
            node.node_name,
            local_line,
        )

    logger.info(
        "map_errors: %d/%d error(s) successfully mapped",
        len(mappings),
        len(errors),
    )
    return mappings
