"""Writes patched code nodes back into a new _fixed.egp ZIP — never touches the original."""

import io
import logging
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)

# Strips XML namespace braces: "{urn:something}Tag" -> "Tag"
_NS_STRIP_RE = re.compile(r"\{[^}]+\}")

_CODE_TAGS = frozenset(
    {"Code", "SourceCode", "SASCode", "CodeTemplate", "EmbeddedSASCode"}
)

_XML_DECLARATION = b"<?xml"


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

@dataclass
class PatchResult:
    node_id: str
    xml_file: str
    success: bool
    error_message: str | None  # None when success is True


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _derive_output_path(egp_path: str) -> str:
    """Insert '_fixed' before the .egp extension: 'project.egp' -> 'project_fixed.egp'."""
    p = Path(egp_path)
    return str(p.with_stem(p.stem + "_fixed"))


def _register_namespaces(raw_bytes: bytes) -> None:
    """Pre-register all namespaces from raw_bytes to prevent ns0/ns1 mangling."""
    for event, elem in ET.iterparse(io.BytesIO(raw_bytes), events=["start-ns"]):
        prefix, uri = elem
        try:
            ET.register_namespace(prefix, uri)
        except ValueError:
            pass


def _walk_and_patch(
    element: ET.Element,
    patch_lookup: dict[str, str],
    xml_file: str,
    results: list[PatchResult],
) -> None:
    """Recursively walk the element tree, replacing code text for matched node IDs."""
    bare_tag = _NS_STRIP_RE.sub("", element.tag)

    if bare_tag in _CODE_TAGS:
        node_id = (
            element.get("id")
            or element.get("Id")
            or element.get("name")
            or element.get("Name")
            or ""
        )
        if node_id in patch_lookup:
            element.text = patch_lookup[node_id]
            results.append(
                PatchResult(
                    node_id=node_id,
                    xml_file=xml_file,
                    success=True,
                    error_message=None,
                )
            )
            logger.debug("Patched node %r in %s", node_id, xml_file)
            return  # children of a patched code element need no further walk

    for child in element:
        _walk_and_patch(child, patch_lookup, xml_file, results)


def _patch_xml_file(
    raw_bytes: bytes,
    patches: list[dict],
) -> tuple[bytes, list[PatchResult]]:
    """Parse raw_bytes, apply patches, and return (patched_bytes, results).

    The XML declaration is preserved when present in the original bytes.
    """
    xml_file = patches[0].get("xml_file", "<unknown>") if patches else "<unknown>"
    results: list[PatchResult] = []

    _register_namespaces(raw_bytes)

    try:
        root = ET.fromstring(raw_bytes)
    except ET.ParseError as exc:
        for patch in patches:
            results.append(
                PatchResult(
                    node_id=patch.get("node_id", ""),
                    xml_file=xml_file,
                    success=False,
                    error_message=f"XML parse error: {exc}",
                )
            )
        return raw_bytes, results

    patch_lookup: dict[str, str] = {
        p["node_id"]: p["new_code"] for p in patches if "node_id" in p and "new_code" in p
    }

    _walk_and_patch(root, patch_lookup, xml_file, results)

    # Record failure for any patch that _walk_and_patch never matched
    matched_ids = {r.node_id for r in results if r.success}
    for node_id in patch_lookup:
        if node_id not in matched_ids:
            results.append(
                PatchResult(
                    node_id=node_id,
                    xml_file=xml_file,
                    success=False,
                    error_message=f"Node ID '{node_id}' not found in {xml_file}",
                )
            )
            logger.warning("Node ID %r not found in %s", node_id, xml_file)

    serialized = ET.tostring(root, encoding="unicode").encode("utf-8")

    if raw_bytes.lstrip().startswith(_XML_DECLARATION):
        declaration = b'<?xml version="1.0" encoding="UTF-8"?>\n'
        patched_bytes = declaration + serialized
    else:
        patched_bytes = serialized

    return patched_bytes, results


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def apply_patches(
    egp_path: str,
    patches: list[dict],
    output_path: str | None = None,
) -> tuple[str, list[PatchResult]]:
    """Write a new _fixed.egp with the given code patches applied.

    The original .egp is never modified. Each patch dict must contain:
      - "node_id"  : str  — the XML attribute id/name of the code element
      - "xml_file" : str  — the ZIP member that contains the element
      - "new_code" : str  — the replacement code text

    Returns (output_path, list[PatchResult]).
    """
    if output_path is None:
        output_path = _derive_output_path(egp_path)

    all_results: list[PatchResult] = []

    # ------------------------------------------------------------------
    # Load all ZIP members into memory first
    # ------------------------------------------------------------------
    try:
        with zipfile.ZipFile(egp_path, "r") as zf:
            archive: dict[str, bytes] = {
                name: zf.read(name) for name in zf.namelist()
            }
    except (FileNotFoundError, zipfile.BadZipFile) as exc:
        logger.error("Cannot open source EGP %s: %s", egp_path, exc)
        raise

    # ------------------------------------------------------------------
    # Group patches by xml_file
    # ------------------------------------------------------------------
    patches_by_file: dict[str, list[dict]] = {}
    for patch in patches:
        xml_file = patch.get("xml_file", "")
        patches_by_file.setdefault(xml_file, []).append(patch)

    # ------------------------------------------------------------------
    # Patch each XML file that has work to do
    # ------------------------------------------------------------------
    for xml_file, file_patches in patches_by_file.items():
        if xml_file not in archive:
            for patch in file_patches:
                msg = f"'{xml_file}' not found in {egp_path}"
                logger.warning(msg)
                all_results.append(
                    PatchResult(
                        node_id=patch.get("node_id", ""),
                        xml_file=xml_file,
                        success=False,
                        error_message=msg,
                    )
                )
            continue

        try:
            patched_bytes, file_results = _patch_xml_file(
                archive[xml_file], file_patches
            )
            archive[xml_file] = patched_bytes
            all_results.extend(file_results)
        except Exception as exc:
            for patch in file_patches:
                msg = f"Unexpected error patching {xml_file}: {exc}"
                logger.error(msg)
                all_results.append(
                    PatchResult(
                        node_id=patch.get("node_id", ""),
                        xml_file=xml_file,
                        success=False,
                        error_message=msg,
                    )
                )

    # ------------------------------------------------------------------
    # Write the new ZIP
    # ------------------------------------------------------------------
    try:
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as out_zf:
            for name, data in archive.items():
                out_zf.writestr(name, data)
    except OSError as exc:
        logger.error("Failed to write output EGP %s: %s", output_path, exc)
        raise

    logger.info(
        "apply_patches: wrote %s (%d patch(es), %d succeeded)",
        output_path,
        len(patches),
        sum(1 for r in all_results if r.success),
    )
    return output_path, all_results
