"""Tests for parse_catalog_dump.py — LuaParser, strip_wow_formatting, parse_source_text."""

import pytest

from parse_catalog_dump import LuaParser, parse_lua_saved_vars, strip_wow_formatting, parse_source_text


# ======================================================================
# LuaParser.parse_string
# ======================================================================

class TestParseString:
    def test_simple_string(self):
        p = LuaParser('"hello"')
        assert p.parse_string() == "hello"

    def test_empty_string(self):
        p = LuaParser('""')
        assert p.parse_string() == ""

    def test_escaped_quote(self):
        p = LuaParser(r'"say \"hi\""')
        assert p.parse_string() == 'say "hi"'

    def test_escaped_backslash(self):
        p = LuaParser(r'"a\\b"')
        assert p.parse_string() == "a\\b"

    def test_escaped_newline(self):
        p = LuaParser(r'"line1\nline2"')
        assert p.parse_string() == "line1\nline2"

    def test_escaped_tab(self):
        p = LuaParser(r'"col1\tcol2"')
        assert p.parse_string() == "col1\tcol2"

    def test_escaped_single_quote(self):
        p = LuaParser(r'''"it\'s"''')
        assert p.parse_string() == "it's"

    def test_decimal_escape_three_digits(self):
        # \065 = 'A'
        p = LuaParser(r'"\065"')
        assert p.parse_string() == "A"

    def test_decimal_escape_two_digits(self):
        # \10 = newline (chr(10))
        p = LuaParser(r'"\10"')
        assert p.parse_string() == "\n"

    def test_decimal_escape_one_digit(self):
        p = LuaParser(r'"\0"')
        assert p.parse_string() == "\x00"

    def test_escaped_carriage_return_discarded(self):
        """\\r should be silently discarded (not appended as literal 'r')."""
        p = LuaParser(r'"Leaf None Behind\r"')
        assert p.parse_string() == "Leaf None Behind"

    def test_control_char_escapes_discarded(self):
        """\\a, \\b, \\f, \\v should be silently discarded."""
        p = LuaParser(r'"hello\aworld\b\f\v"')
        assert p.parse_string() == "helloworld"

    def test_crlf_keeps_only_newline(self):
        """\\r\\n should produce just \\n (CR discarded, LF kept)."""
        p = LuaParser(r'"line1\r\nline2"')
        assert p.parse_string() == "line1\nline2"

    def test_string_with_wow_escape_sequences(self):
        p = LuaParser('"|cFFFFD200Quest: |rDecor Hunt"')
        assert p.parse_string() == "|cFFFFD200Quest: |rDecor Hunt"

    def test_unterminated_string_raises(self):
        p = LuaParser('"no end')
        with pytest.raises(ValueError, match="Unterminated string"):
            p.parse_string()


# ======================================================================
# LuaParser.parse_number
# ======================================================================

class TestParseNumber:
    def test_integer(self):
        p = LuaParser("42")
        assert p.parse_number() == 42

    def test_zero(self):
        p = LuaParser("0")
        assert p.parse_number() == 0

    def test_negative_integer(self):
        p = LuaParser("-7")
        assert p.parse_number() == -7

    def test_float(self):
        p = LuaParser("3.14")
        assert p.parse_number() == pytest.approx(3.14)

    def test_negative_float(self):
        p = LuaParser("-0.5")
        assert p.parse_number() == pytest.approx(-0.5)

    def test_hex_number(self):
        p = LuaParser("0xFF")
        assert p.parse_number() == 255

    def test_hex_lowercase(self):
        p = LuaParser("0x1a")
        assert p.parse_number() == 26

    def test_scientific_notation(self):
        p = LuaParser("1e3")
        assert p.parse_number() == pytest.approx(1000.0)

    def test_scientific_notation_negative_exp(self):
        p = LuaParser("5e-2")
        assert p.parse_number() == pytest.approx(0.05)

    def test_large_integer(self):
        p = LuaParser("123456789")
        assert p.parse_number() == 123456789

    def test_invalid_number_raises(self):
        p = LuaParser("abc")
        with pytest.raises(ValueError, match="Expected number"):
            p.parse_number()


# ======================================================================
# LuaParser.parse_table
# ======================================================================

class TestParseTable:
    def test_empty_table(self):
        p = LuaParser("{}")
        assert p.parse_table() == {}

    def test_array_table(self):
        p = LuaParser("{1, 2, 3}")
        assert p.parse_table() == [1, 2, 3]

    def test_array_of_strings(self):
        p = LuaParser('{"a", "b", "c"}')
        assert p.parse_table() == ["a", "b", "c"]

    def test_dict_table_string_keys(self):
        p = LuaParser('{ name = "test", value = 42 }')
        result = p.parse_table()
        assert result == {"name": "test", "value": 42}

    def test_dict_table_bracket_string_keys(self):
        p = LuaParser('{ ["key one"] = 1, ["key two"] = 2 }')
        result = p.parse_table()
        assert result == {"key one": 1, "key two": 2}

    def test_dict_table_bracket_int_keys(self):
        p = LuaParser("{ [1] = 10, [2] = 20 }")
        result = p.parse_table()
        assert result == {1: 10, 2: 20}

    def test_nested_table(self):
        p = LuaParser('{ inner = { x = 1, y = 2 } }')
        result = p.parse_table()
        assert result == {"inner": {"x": 1, "y": 2}}

    def test_nested_array(self):
        p = LuaParser("{ {1, 2}, {3, 4} }")
        result = p.parse_table()
        assert result == [[1, 2], [3, 4]]

    def test_mixed_table(self):
        """Array entries + dict entries produce a merged dict with int keys for array part."""
        p = LuaParser('{ "first", name = "test" }')
        result = p.parse_table()
        assert result == {1: "first", "name": "test"}

    def test_trailing_comma(self):
        p = LuaParser("{1, 2, 3,}")
        assert p.parse_table() == [1, 2, 3]

    def test_semicolon_separator(self):
        p = LuaParser("{1; 2; 3}")
        assert p.parse_table() == [1, 2, 3]

    def test_boolean_values(self):
        p = LuaParser("{ a = true, b = false }")
        result = p.parse_table()
        assert result == {"a": True, "b": False}

    def test_nil_value(self):
        p = LuaParser("{ a = nil }")
        result = p.parse_table()
        assert result == {"a": None}

    def test_complex_nested(self):
        lua = '{ decorID = 100, name = "Lamp", categoryIDs = {5, 10}, sources = { { type = "Quest", value = "Test" } } }'
        p = LuaParser(lua)
        result = p.parse_table()
        assert result["decorID"] == 100
        assert result["name"] == "Lamp"
        assert result["categoryIDs"] == [5, 10]
        assert result["sources"] == [{"type": "Quest", "value": "Test"}]


# ======================================================================
# LuaParser._skip_whitespace_and_comments
# ======================================================================

class TestSkipWhitespaceAndComments:
    def test_skip_spaces(self):
        p = LuaParser("   42")
        p._skip_whitespace_and_comments()
        assert p.text[p.pos:] == "42"

    def test_skip_single_line_comment(self):
        p = LuaParser("-- comment\n42")
        p._skip_whitespace_and_comments()
        assert p.text[p.pos:] == "42"

    def test_skip_block_comment(self):
        p = LuaParser("--[[ block comment ]]42")
        p._skip_whitespace_and_comments()
        assert p.text[p.pos:] == "42"

    def test_skip_multiple_comments(self):
        p = LuaParser("-- line1\n-- line2\n42")
        p._skip_whitespace_and_comments()
        assert p.text[p.pos:] == "42"

    def test_skip_mixed_whitespace_and_comments(self):
        p = LuaParser("  \n\t-- comment\n  --[[ block ]]  99")
        p._skip_whitespace_and_comments()
        assert p.text[p.pos:] == "99"

    def test_comment_inside_table(self):
        p = LuaParser("{ -- items\n 1, 2 }")
        result = p.parse_table()
        assert result == [1, 2]

    def test_block_comment_inside_table(self):
        p = LuaParser("{ --[[ stuff ]] 1, 2 }")
        result = p.parse_table()
        assert result == [1, 2]


# ======================================================================
# parse_lua_saved_vars
# ======================================================================

class TestParseLuaSavedVars:
    def test_single_variable(self):
        text = 'MyAddonDB = {\n  ["key"] = "value",\n}'
        result = parse_lua_saved_vars(text)
        assert "MyAddonDB" in result
        assert result["MyAddonDB"] == {"key": "value"}

    def test_multiple_variables(self):
        text = 'VarA = {\n  x = 1,\n}\nVarB = {\n  y = 2,\n}'
        result = parse_lua_saved_vars(text)
        assert result["VarA"] == {"x": 1}
        assert result["VarB"] == {"y": 2}

    def test_hearth_and_seek_style(self):
        text = '''HearthAndSeekDB = {
    ["catalogDump"] = {
        {
            ["decorID"] = 100,
            ["name"] = "Test Lamp",
        },
        {
            ["decorID"] = 200,
            ["name"] = "Test Chair",
        },
    },
}'''
        result = parse_lua_saved_vars(text)
        db = result["HearthAndSeekDB"]
        assert "catalogDump" in db
        dump = db["catalogDump"]
        assert len(dump) == 2
        assert dump[0]["decorID"] == 100
        assert dump[1]["name"] == "Test Chair"

    def test_empty_table(self):
        text = "EmptyDB = {}"
        result = parse_lua_saved_vars(text)
        assert result["EmptyDB"] == {}

    def test_with_comments(self):
        text = "-- SavedVariables\nMyDB = {\n  val = 42, -- inline\n}"
        result = parse_lua_saved_vars(text)
        assert result["MyDB"] == {"val": 42}

    def test_no_assignments(self):
        text = "-- just a comment\n"
        result = parse_lua_saved_vars(text)
        assert result == {}


# ======================================================================
# strip_wow_formatting
# ======================================================================

class TestStripWowFormatting:
    def test_color_code(self):
        assert strip_wow_formatting("|cFFFFD200Quest:|r") == "Quest:"

    def test_color_with_text(self):
        result = strip_wow_formatting("|cFFFFD200Quest: |rDecor Hunt")
        assert result == "Quest: Decor Hunt"

    def test_newline_escape(self):
        result = strip_wow_formatting("Line1|nLine2")
        assert result == "Line1\nLine2"

    def test_texture_tag(self):
        result = strip_wow_formatting("|Tinterface/icons/inv_misc_coin_01.blp:16|t")
        assert result == ""

    def test_hyperlink(self):
        result = strip_wow_formatting("|Hcurrency:824|h|Tpath:0|t|h")
        assert "|H" not in result
        assert "|h" not in result

    def test_combined_formatting(self):
        raw = "|cFFFFD200Quest: |rDecor Hunt|n|cFFFFD200Zone: |rElwynn Forest"
        result = strip_wow_formatting(raw)
        assert result == "Quest: Decor Hunt\nZone: Elwynn Forest"

    def test_empty_string(self):
        assert strip_wow_formatting("") == ""

    def test_none_input(self):
        assert strip_wow_formatting(None) == ""

    def test_plain_text_unchanged(self):
        assert strip_wow_formatting("Hello World") == "Hello World"

    def test_multiple_color_codes(self):
        raw = "|cFF00FF00Green|r and |cFFFF0000Red|r"
        result = strip_wow_formatting(raw)
        assert result == "Green and Red"

    def test_color_reset_only(self):
        assert strip_wow_formatting("|r") == ""

    def test_whitespace_collapse(self):
        result = strip_wow_formatting("word1   word2\t\tword3")
        assert result == "word1 word2 word3"


# ======================================================================
# parse_source_text
# ======================================================================

class TestParseSourceText:
    def test_quest_source(self):
        raw = "|cFFFFD200Quest: |rDecor Treasure Hunt|n|cFFFFD200Zone: |rFounder's Point"
        result = parse_source_text(raw)
        assert result["quest"] == "Decor Treasure Hunt"
        assert result["zone"] == "Founder's Point"
        assert any(s["type"] == "Quest" for s in result["sources"])

    def test_vendor_source(self):
        raw = "|cFFFFD200Vendor: |rCaptain Lancy|n|cFFFFD200Zone: |rElwynn Forest"
        result = parse_source_text(raw)
        assert result["vendor"] == "Captain Lancy"
        assert result["zone"] == "Elwynn Forest"
        assert any(s["type"] == "Vendor" for s in result["sources"])

    def test_achievement_source(self):
        raw = "|cFFFFD200Achievement: |rDecor Collector"
        result = parse_source_text(raw)
        assert result["achievement"] == "Decor Collector"
        assert any(s["type"] == "Achievement" for s in result["sources"])

    def test_profession_source(self):
        raw = "|cFFFFD200Profession: |rMidnight Tailoring (50)"
        result = parse_source_text(raw)
        assert result["profession"] == "Midnight Tailoring (50)"
        assert any(s["type"] == "Profession" for s in result["sources"])

    def test_multiple_sources(self):
        raw = "|cFFFFD200Quest: |rSome Quest|n|cFFFFD200Vendor: |rSome Vendor|n|cFFFFD200Zone: |rSome Zone"
        result = parse_source_text(raw)
        assert result["quest"] == "Some Quest"
        assert result["vendor"] == "Some Vendor"
        assert result["zone"] == "Some Zone"
        types = [s["type"] for s in result["sources"]]
        assert "Quest" in types
        assert "Vendor" in types

    def test_empty_input(self):
        assert parse_source_text("") == {}
        assert parse_source_text(None) == {}

    def test_drop_source(self):
        raw = "|cFFFFD200Drop: |rSome Boss|n|cFFFFD200Zone: |rSome Dungeon"
        result = parse_source_text(raw)
        assert result["drop"] == "Some Boss"
        assert any(s["type"] == "Drop" for s in result["sources"])

    def test_treasure_source(self):
        raw = "|cFFFFD200Treasure: |rHidden Chest"
        result = parse_source_text(raw)
        assert result["treasure"] == "Hidden Chest"
        assert any(s["type"] == "Treasure" for s in result["sources"])

    def test_vendors_plural(self):
        raw = "|cFFFFD200Vendors: |rMultiple Vendors"
        result = parse_source_text(raw)
        assert result["vendor"] == "Multiple Vendors"
        assert any(s["type"] == "Vendor" for s in result["sources"])

    def test_shop_source(self):
        raw = "|cFFFFD200Shop: |rBlizzard Shop"
        result = parse_source_text(raw)
        assert any(s["type"] == "Shop" for s in result["sources"])

    def test_unknown_key(self):
        raw = "|cFFFFD200SomeKey: |rSomeValue"
        result = parse_source_text(raw)
        assert any(s["type"] == "SomeKey" for s in result["sources"])

    def test_line_without_colon(self):
        """A line with no colon becomes an Unknown source."""
        result = parse_source_text("Just some text")
        assert any(s["type"] == "Unknown" for s in result["sources"])

    def test_cost_line_skipped(self):
        raw = "|cFFFFD200Cost: |r100 Gold"
        result = parse_source_text(raw)
        # Cost lines are skipped, should not appear as a source
        assert "cost" not in result
        assert not any(s["type"] == "Cost" for s in result["sources"])

    def test_first_vendor_kept(self):
        raw = "|cFFFFD200Vendor: |rFirst Vendor|n|cFFFFD200Vendor: |rSecond Vendor"
        result = parse_source_text(raw)
        assert result["vendor"] == "First Vendor"
