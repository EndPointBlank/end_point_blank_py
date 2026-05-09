from __future__ import annotations

import xml.etree.ElementTree as ET

from .string_truncator import StringTruncator

MAX_BYTES = 10_000
MAX_DEPTH = 6
MAX_CHILDREN = 20
MAX_ATTRIBUTES = 20
MAX_TEXT = 200
TEXT_SUFFIX = "..."


class XmlTruncator:
    """
    Truncates an XML string to fit within a byte budget using the standard-library
    ``xml.etree.ElementTree`` parser.

    Equivalent to the Ruby gem's ``XmlTruncator``.
    """

    @classmethod
    def truncate(cls, xml: str | None, limit: int = MAX_BYTES) -> str:
        if not xml:
            return ""
        text = str(xml)
        if len(text.encode("utf-8")) <= limit:
            return text

        root = cls._parse(text)
        if root is None:
            return StringTruncator.truncate(text, limit=limit, suffix="<truncated/>")

        pruned = cls._prune_element(root, depth=0)
        output = ET.tostring(pruned, encoding="unicode")
        if len(output.encode("utf-8")) <= limit:
            return output

        compact = f"<{root.tag}><truncated/></{root.tag}>"
        if len(compact.encode("utf-8")) <= limit:
            return compact

        return "<truncated/>"

    @classmethod
    def _parse(cls, text: str) -> ET.Element | None:
        try:
            return ET.fromstring(text)
        except ET.ParseError:
            return None

    @classmethod
    def _prune_element(cls, element: ET.Element, depth: int) -> ET.Element:
        pruned = ET.Element(element.tag)

        # Copy attributes (up to MAX_ATTRIBUTES)
        for i, (name, value) in enumerate(element.attrib.items()):
            if i >= MAX_ATTRIBUTES:
                break
            pruned.set(name, cls._truncate_text(value, max_len=100))

        if depth >= MAX_DEPTH:
            ET.SubElement(pruned, "truncated")
            return pruned

        pruned.text = cls._truncate_text(element.text) if element.text else None
        pruned.tail = None  # tails belong to the parent element

        child_count = 0
        for child in element:
            if child_count >= MAX_CHILDREN:
                ET.SubElement(pruned, "truncated")
                break
            pruned.append(cls._prune_element(child, depth + 1))
            child_count += 1

        return pruned

    @staticmethod
    def _truncate_text(text: str | None, max_len: int = MAX_TEXT) -> str:
        if not text:
            return ""
        encoded = text.encode("utf-8")
        if len(encoded) <= max_len:
            return text
        sliced = encoded[: max_len - len(TEXT_SUFFIX.encode("utf-8"))].decode(
            "utf-8", errors="ignore"
        )
        return sliced + TEXT_SUFFIX
