"""T016: ADF conversion in both directions (research R1)."""

from __future__ import annotations

from typing import Any

import pytest

from jira_testgen.jira.adf import adf_to_text, build_test_case_adf

pytestmark = pytest.mark.unit


def doc(*content: dict[str, Any]) -> dict[str, Any]:
    return {"type": "doc", "version": 1, "content": list(content)}


def para(text: str) -> dict[str, Any]:
    return {"type": "paragraph", "content": [{"type": "text", "text": text}]}


class TestExtraction:
    def test_plain_paragraph(self) -> None:
        assert adf_to_text(doc(para("Hello world"))).text == "Hello world"

    def test_multiple_paragraphs(self) -> None:
        result = adf_to_text(doc(para("First"), para("Second")))
        assert result.text == "First\nSecond"

    def test_heading(self) -> None:
        node = {
            "type": "heading",
            "attrs": {"level": 2},
            "content": [{"type": "text", "text": "AC"}],
        }
        assert "## AC" in adf_to_text(doc(node)).text

    def test_bullet_list(self) -> None:
        node = {
            "type": "bulletList",
            "content": [
                {"type": "listItem", "content": [para("alpha")]},
                {"type": "listItem", "content": [para("beta")]},
            ],
        }
        text = adf_to_text(doc(node)).text
        assert "- alpha" in text
        assert "- beta" in text

    def test_ordered_list_numbers_sequentially(self) -> None:
        node = {
            "type": "orderedList",
            "content": [
                {"type": "listItem", "content": [para("one")]},
                {"type": "listItem", "content": [para("two")]},
                {"type": "listItem", "content": [para("three")]},
            ],
        }
        text = adf_to_text(doc(node)).text
        assert "1. one" in text
        assert "2. two" in text
        assert "3. three" in text

    def test_nested_list(self) -> None:
        inner = {
            "type": "bulletList",
            "content": [{"type": "listItem", "content": [para("child")]}],
        }
        outer = {
            "type": "bulletList",
            "content": [{"type": "listItem", "content": [para("parent"), inner]}],
        }
        text = adf_to_text(doc(outer)).text
        assert "parent" in text
        assert "child" in text

    def test_code_block(self) -> None:
        node = {"type": "codeBlock", "content": [{"type": "text", "text": "GET /x"}]}
        text = adf_to_text(doc(node)).text
        assert "```" in text
        assert "GET /x" in text

    def test_blockquote(self) -> None:
        node = {"type": "blockquote", "content": [para("quoted")]}
        assert "> quoted" in adf_to_text(doc(node)).text

    def test_table_renders_rows(self) -> None:
        node = {
            "type": "table",
            "content": [
                {
                    "type": "tableRow",
                    "content": [
                        {"type": "tableHeader", "content": [para("Input")]},
                        {"type": "tableHeader", "content": [para("Output")]},
                    ],
                },
                {
                    "type": "tableRow",
                    "content": [
                        {"type": "tableCell", "content": [para("empty")]},
                        {"type": "tableCell", "content": [para("error")]},
                    ],
                },
            ],
        }
        text = adf_to_text(doc(node)).text
        assert "Input | Output" in text
        assert "empty | error" in text

    def test_hard_break_and_rule(self) -> None:
        text = adf_to_text(doc(para("a"), {"type": "rule"}, para("b"))).text
        assert "---" in text
        # A rule is structural, not unreadable content.
        assert adf_to_text(doc({"type": "rule"})).unread_node_types == []

    def test_accepts_plain_string(self) -> None:
        """Some Jira fields are plain text; callers should not have to branch."""
        assert adf_to_text("just text").text == "just text"

    def test_accepts_none(self) -> None:
        result = adf_to_text(None)
        assert result.text == ""
        assert result.unread_node_types == []


class TestUnreadableNodes:
    """FR-009: content we cannot render must be reported, never silently dropped."""

    def test_media_is_reported(self) -> None:
        node = {"type": "mediaSingle", "content": [{"type": "media", "attrs": {"id": "x"}}]}
        result = adf_to_text(doc(para("See diagram:"), node))
        assert "See diagram:" in result.text
        assert "mediaSingle" in result.unread_node_types

    def test_multiple_unknown_types_deduplicated_and_sorted(self) -> None:
        result = adf_to_text(
            doc(
                {"type": "mediaGroup", "content": []},
                {"type": "inlineCard", "content": []},
                {"type": "mediaGroup", "content": []},
            )
        )
        assert result.unread_node_types == ["inlineCard", "mediaGroup"]

    def test_descends_into_unknown_wrapper_to_recover_text(self) -> None:
        """An unknown wrapper may still contain readable text -- take it and flag the wrapper."""
        node = {"type": "expand", "content": [para("hidden but readable")]}
        result = adf_to_text(doc(node))
        assert "hidden but readable" in result.text
        assert "expand" in result.unread_node_types


class TestBuilder:
    def _case(self, **kw: Any) -> dict[str, Any]:
        defaults: dict[str, Any] = {
            "preconditions": "A user exists.",
            "steps": ["Open page", "Submit"],
            "expected_results": ["Page loads", "Error shows"],
            "traces_to": ["AC-1"],
            "source_issue_key": "PROJ-123",
        }
        defaults.update(kw)
        return build_test_case_adf(**defaults)

    def test_shape_is_a_valid_adf_document(self) -> None:
        body = self._case()
        assert body["type"] == "doc"
        assert body["version"] == 1
        assert isinstance(body["content"], list)

    def test_includes_all_sections(self) -> None:
        text = adf_to_text(self._case()).text
        assert "Preconditions" in text
        assert "Steps" in text
        assert "Expected results" in text
        assert "Traceability" in text

    def test_omits_preconditions_when_empty(self) -> None:
        text = adf_to_text(self._case(preconditions="   ")).text
        assert "Preconditions" not in text

    def test_steps_are_an_ordered_list(self) -> None:
        body = self._case()
        kinds = [block["type"] for block in body["content"]]
        assert "orderedList" in kinds

    def test_single_overall_result_renders_as_paragraph(self) -> None:
        body = self._case(steps=["a", "b", "c"], expected_results=["All good"])
        text = adf_to_text(body).text
        assert "All good" in text
        assert "1. All good" not in text

    def test_round_trip_preserves_step_text(self) -> None:
        steps = ["Open the reset page", "Enter an unregistered email", "Submit the form"]
        text = adf_to_text(self._case(steps=steps, expected_results=["Generic notice"])).text
        for step in steps:
            assert step in text
