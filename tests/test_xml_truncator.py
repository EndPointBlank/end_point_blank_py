"""
``XmlTruncator`` is the XML counterpart to ``FastJsonTruncator``. It has an extra
failure mode the JSON one does not: pruning must leave the result parseable, or
the recorded body is unreadable in the portal.
"""

import xml.etree.ElementTree as ET

from end_point_blank.xml_truncator import XmlTruncator

FILLER = "x" * 4000


def parsed(xml, **kwargs):
    return ET.fromstring(XmlTruncator.truncate(xml, **kwargs))


class TestWhenNoTruncationIsNeeded:
    def test_returns_an_empty_string_for_none(self):
        assert XmlTruncator.truncate(None) == ""

    def test_returns_an_empty_string_for_blank_input(self):
        assert XmlTruncator.truncate("") == ""

    def test_leaves_a_small_document_byte_for_byte_alone(self):
        document = "<root><child>value</child></root>"

        assert XmlTruncator.truncate(document) == document


class TestPruning:
    def test_the_result_is_still_parseable_xml(self):
        # Everything else here depends on this: a pruned document that no longer
        # parses is worse than no body at all.
        element = parsed(f"<root><a>1</a><b>{FILLER}</b></root>", limit=2000)

        assert element.tag == "root"

    def test_keeps_the_root_element(self):
        assert parsed(f"<orders>{FILLER}</orders>", limit=2000).tag == "orders"

    def test_shortens_long_element_text(self):
        text = parsed(f"<root>{FILLER}</root>", limit=2000).text

        assert text.endswith("...")
        assert len(text.encode("utf-8")) <= 200

    def test_keeps_the_first_twenty_children_and_marks_the_rest(self):
        children = "".join(f"<c{i}>v</c{i}>" for i in range(50))
        element = parsed(f"<root>{children}<pad>{FILLER}</pad></root>", limit=3000)

        tags = [child.tag for child in element]

        assert tags[:3] == ["c0", "c1", "c2"]
        assert len(tags) == 21
        assert tags[-1] == "truncated"

    def test_keeps_the_first_twenty_attributes(self):
        attrs = " ".join(f'a{i}="v{i}"' for i in range(50))
        element = parsed(f"<root {attrs}>{FILLER}</root>", limit=3000)

        assert len(element.attrib) == 20
        assert element.attrib["a0"] == "v0"
        assert "a20" not in element.attrib

    def test_keeps_an_empty_attribute(self):
        element = parsed(f'<root flag="" note="x">{FILLER}</root>', limit=2000)

        assert element.attrib["flag"] == ""

    def test_shortens_a_long_attribute_value(self):
        element = parsed(f'<root note="{FILLER}">text</root>', limit=2000)

        assert element.attrib["note"].endswith("...")
        assert len(element.attrib["note"].encode("utf-8")) <= 100

    def test_marks_the_cut_where_nesting_gets_too_deep(self):
        document = "".join(f"<l{i}>" for i in range(12)) + FILLER + "".join(f"</l{i}>" for i in reversed(range(12)))

        element = parsed(document, limit=2000)
        depth = 0
        while len(element):
            element = element[0]
            depth += 1

        assert element.tag == "truncated"
        assert depth == 7


class TestFallbacks:
    def test_falls_back_to_plain_truncation_for_unparseable_input(self):
        # Bodies are recorded whatever they are; a malformed XML body must still
        # produce a bounded record rather than an exception.
        result = XmlTruncator.truncate("<not valid xml " + "z" * 4000, limit=100)

        assert result.endswith("<truncated/>")
        assert len(result.encode("utf-8")) <= 100

    def test_collapses_to_an_empty_root_when_pruning_is_not_enough(self):
        result = XmlTruncator.truncate(f"<root>{FILLER}</root>", limit=30)

        assert result == "<root><truncated/></root>"

    def test_gives_up_entirely_when_even_the_root_will_not_fit(self):
        result = XmlTruncator.truncate(f"<a-very-long-root-element-name>{FILLER}</a-very-long-root-element-name>", limit=15)

        assert result == "<truncated/>"
