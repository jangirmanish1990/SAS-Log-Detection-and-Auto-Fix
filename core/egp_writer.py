"""Writes patched code nodes back into a new _fixed.egp ZIP — never touches the original."""

import io
import logging
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)

# Strips XML namespace braces: "{urn:something}Tag" -> "Tag"
_NS_STRIP_RE = re.compile(r"\{[^}]+\}")

_CODE_TAGS = frozenset(
    {"Code", "SourceCode", "SASCode", "CodeTemplate", "EmbeddedSASCode"}
)

_XML_DECLARATION = b"<?xml"

# Template for locating a code element by its own id/name attribute in raw XML text.
# "NODE_ID" is a literal placeholder — replaced via str.replace() before compiling.
# Groups: (1) opening tag, (2) element content, (3) closing tag.
_CODE_ELEM_PATTERN = (
    r"(<(?:\w+:)?(?:Code|SourceCode|SASCode|CodeTemplate|EmbeddedSASCode)"
    r"\b[^>]*(?:id|Id|name|Name)=[\"']NODE_ID[\"'][^>]*>)"
    r"(.*?)"
    r"(</(?:\w+:)?(?:Code|SourceCode|SASCode|CodeTemplate|EmbeddedSASCode)>)"
)


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
            ET.register_namespace(str(prefix), str(uri))
        except ValueError:
            pass


def _replace_cdata_content(
    xml_str: str, node_id: str, new_code: str
) -> tuple[str, bool]:
    """Find a code element by id/name in the raw XML string and replace its content.

    The replacement is wrapped in CDATA to preserve all SAS source characters
    without entity encoding. Returns (updated_xml_str, matched).
    """
    pattern = re.compile(
        _CODE_ELEM_PATTERN.replace("NODE_ID", re.escape(node_id)),
        re.DOTALL,
    )
    new_xml, count = pattern.subn(
        lambda m: m.group(1) + "<![CDATA[" + new_code + "]]>" + m.group(3),
        xml_str,
        count=1,
    )
    return new_xml, count > 0


def _walk_and_patch(
    element: ET.Element,
    patch_lookup: dict[str, str],
    xml_file: str,
    results: list[PatchResult],
    parent_map: dict[ET.Element, ET.Element],
) -> None:
    """Recursively walk the element tree, replacing code text for matched node IDs.

    When a code element's own attributes don't match any patch key, walks up
    the parent chain (max 3 levels) to find an ancestor whose id/name matches —
    mirroring the ancestor-walk that egp_mapper uses when the code element itself
    carries no id attribute.
    """
    bare_tag = _NS_STRIP_RE.sub("", element.tag)

    if bare_tag in _CODE_TAGS:
        node_id = (
            element.get("id")
            or element.get("Id")
            or element.get("name")
            or element.get("Name")
            or ""
        )

        # Ancestor walk: if own id is absent/unrecognised, try parents (max 3 up)
        if node_id not in patch_lookup:
            ancestor: ET.Element | None = parent_map.get(element)
            levels = 0
            while ancestor is not None and levels < 3:
                anc_id = (
                    ancestor.get("id")
                    or ancestor.get("Id")
                    or ancestor.get("name")
                    or ancestor.get("Name")
                    or ""
                )
                if anc_id in patch_lookup:
                    node_id = anc_id
                    break
                ancestor = parent_map.get(ancestor)
                levels += 1

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
        _walk_and_patch(child, patch_lookup, xml_file, results, parent_map)


def _patch_xml_file(
    raw_bytes: bytes,
    patches: list[dict[str, Any]],
) -> tuple[bytes, list[PatchResult]]:
    """Parse raw_bytes, apply patches, and return (patched_bytes, results).

    Two code paths:
    - CDATA detected  : string-replacement via regex, new content wrapped in CDATA.
      Avoids ET round-tripping that silently converts CDATA to entity-escaped text.
    - No CDATA        : ElementTree walk-and-replace (correct entity handling).

    The XML declaration is preserved when present in the original bytes.
    """
    xml_file = patches[0].get("xml_file", "<unknown>") if patches else "<unknown>"
    results: list[PatchResult] = []

    # HIGH #4: reject patches whose node_id is empty or whitespace
    valid_patches: list[dict[str, Any]] = []
    for p in patches:
        nid = (p.get("node_id") or "").strip()
        if not nid:
            logger.warning("Skipping patch with empty node_id in %s", xml_file)
            results.append(
                PatchResult(
                    node_id="",
                    xml_file=xml_file,
                    success=False,
                    error_message="Patch skipped: node_id is empty or whitespace",
                )
            )
        else:
            valid_patches.append(p)
    patches = valid_patches

    # HIGH #5: warn explicitly when duplicate node_ids are present (last one wins)
    seen_ids: dict[str, int] = {}
    for p in patches:
        nid = p.get("node_id", "")
        seen_ids[nid] = seen_ids.get(nid, 0) + 1
    for nid, cnt in seen_ids.items():
        if cnt > 1:
            logger.warning(
                "Duplicate node_id %r appears %d time(s) in %s — keeping last",
                nid,
                cnt,
                xml_file,
            )

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
        p["node_id"]: p["new_code"]
        for p in patches
        if "node_id" in p and "new_code" in p
    }

    # CRITICAL #1: if any CDATA section is present, avoid ET serialisation entirely
    # to prevent silent CDATA→entity conversion across the whole document.
    if b"<![CDATA[" in raw_bytes:
        xml_str = raw_bytes.decode("utf-8")
        for node_id, new_code in patch_lookup.items():
            xml_str, matched = _replace_cdata_content(xml_str, node_id, new_code)
            if matched:
                results.append(
                    PatchResult(
                        node_id=node_id,
                        xml_file=xml_file,
                        success=True,
                        error_message=None,
                    )
                )
                logger.debug("Patched node %r in %s (CDATA path)", node_id, xml_file)
            else:
                results.append(
                    PatchResult(
                        node_id=node_id,
                        xml_file=xml_file,
                        success=False,
                        error_message=f"Node ID '{node_id}' not found in {xml_file}",
                    )
                )
                logger.warning("Node ID %r not found in %s (CDATA path)", node_id, xml_file)
        return xml_str.encode("utf-8"), results

    # Non-CDATA path: ElementTree walk with ancestor-id fallback (CRITICAL #2)
    parent_map: dict[ET.Element, ET.Element] = {c: p for p in root.iter() for c in p}
    _walk_and_patch(root, patch_lookup, xml_file, results, parent_map)

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
    patches: list[dict[str, Any]],
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
    # Load all ZIP members into memory, preserving ZipInfo metadata
    # ------------------------------------------------------------------
    try:
        with zipfile.ZipFile(egp_path, "r") as zf:
            # HIGH #3: keep ZipInfo so timestamps/compression/attrs survive the round-trip
            archive: dict[str, tuple[zipfile.ZipInfo, bytes]] = {
                zi.filename: (zi, zf.read(zi)) for zi in zf.infolist()
            }
    except (FileNotFoundError, zipfile.BadZipFile) as exc:
        logger.error("Cannot open source EGP %s: %s", egp_path, exc)
        raise

    # ------------------------------------------------------------------
    # Group patches by xml_file
    # ------------------------------------------------------------------
    patches_by_file: dict[str, list[dict[str, Any]]] = {}
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
            zip_info, raw_bytes = archive[xml_file]
            patched_bytes, file_results = _patch_xml_file(raw_bytes, file_patches)
            archive[xml_file] = (zip_info, patched_bytes)
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
    # Write the new ZIP, restoring original ZipInfo metadata per member
    # ------------------------------------------------------------------
    try:
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as out_zf:
            for zip_info, data in archive.values():
                out_zf.writestr(zip_info, data)
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
