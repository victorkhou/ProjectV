"""
Unit tests for the shared player-message list-formatting vocabulary in
``world.utils`` — the one look every game list uses:

    |wHeader:|n
      Key - Value

Covers section_header, format_list_row, format_list_block, format_section, and
format_cost_block.
"""

import unittest

from mygame.world.utils import (
    LIST_INDENT,
    LIST_KV_SEP,
    section_header,
    format_list_row,
    format_list_block,
    format_section,
    format_cost_block,
)


class TestSectionHeader(unittest.TestCase):
    def test_plain_title_is_white_bold_with_colon(self):
        self.assertEqual(section_header("Cost"), "|wCost:|n")

    def test_trailing_colon_is_not_doubled(self):
        self.assertEqual(section_header("Cost:"), "|wCost:|n")

    def test_precolored_title_passes_through_with_colon(self):
        # A caller that pre-tints its header keeps that color; we only add ":".
        self.assertEqual(section_header("|rDanger|n"), "|rDanger|n:")


class TestListRow(unittest.TestCase):
    def test_key_value_row(self):
        self.assertEqual(format_list_row("Iron", 20), f"{LIST_INDENT}Iron{LIST_KV_SEP}20")

    def test_key_only_row_when_no_value(self):
        self.assertEqual(format_list_row("Reinforced Walls"), f"{LIST_INDENT}Reinforced Walls")

    def test_zero_value_is_kept_not_collapsed(self):
        # 0 is a real quantity, not "no value".
        self.assertEqual(format_list_row("Stock", 0), f"{LIST_INDENT}Stock{LIST_KV_SEP}0")

    def test_empty_string_value_collapses_to_key_only(self):
        self.assertEqual(format_list_row("Solo", ""), f"{LIST_INDENT}Solo")


class TestListBlock(unittest.TestCase):
    def test_mapping_becomes_kv_rows_in_order(self):
        rows = format_list_block({"Iron": 20, "Energy": 15})
        self.assertEqual(rows, ["  Iron - 20", "  Energy - 15"])

    def test_iterable_of_strings(self):
        rows = format_list_block(["Alpha", "Beta"])
        self.assertEqual(rows, ["  Alpha", "  Beta"])

    def test_iterable_of_pairs(self):
        rows = format_list_block([("Player", "Level 15"), ("HQ", "Level 1")])
        self.assertEqual(rows, ["  Player - Level 15", "  HQ - Level 1"])

    def test_empty_iterable_is_no_rows(self):
        self.assertEqual(format_list_block([]), [])


class TestFormatSection(unittest.TestCase):
    def test_header_plus_rows(self):
        lines = format_section("Cost", {"Iron": 20, "Energy": 15})
        self.assertEqual(lines, ["|wCost:|n", "  Iron - 20", "  Energy - 15"])

    def test_empty_with_placeholder(self):
        lines = format_section("Researched", [], empty="none")
        self.assertEqual(lines, ["|wResearched:|n", "  none"])

    def test_empty_without_placeholder_is_header_only(self):
        lines = format_section("Researched", [])
        self.assertEqual(lines, ["|wResearched:|n"])

    def test_joins_into_the_expected_block(self):
        lines = format_section(
            "Dependencies", [("Player", "Level 15"), ("HQ", "Level 1")]
        )
        self.assertEqual(
            "\n".join(lines),
            "|wDependencies:|n\n  Player - Level 15\n  HQ - Level 1",
        )


class TestCostBlock(unittest.TestCase):
    def test_positive_costs_only(self):
        lines = format_cost_block({"Iron": 20, "Energy": 15, "Wood": 0})
        self.assertEqual(lines, ["|wCost:|n", "  Iron - 20", "  Energy - 15"])

    def test_empty_cost_is_free(self):
        self.assertEqual(format_cost_block({}), ["|wCost:|n", "  free"])

    def test_all_zero_cost_is_free(self):
        self.assertEqual(format_cost_block({"Iron": 0}), ["|wCost:|n", "  free"])


if __name__ == "__main__":
    unittest.main()
