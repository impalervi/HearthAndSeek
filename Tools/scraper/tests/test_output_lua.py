"""Tests for output_lua.py — Lua serialization helpers."""

import pytest

from output_lua import lua_string, lua_number, lua_coords, lua_value


# ======================================================================
# lua_string
# ======================================================================

class TestLuaString:
    def test_simple_string(self):
        assert lua_string("hello") == '"hello"'

    def test_none_returns_nil(self):
        assert lua_string(None) == "nil"

    def test_empty_string(self):
        assert lua_string("") == '""'

    def test_escapes_double_quote(self):
        assert lua_string('say "hi"') == r'"say \"hi\""'

    def test_escapes_backslash(self):
        assert lua_string("a\\b") == r'"a\\b"'

    def test_escapes_newline(self):
        assert lua_string("line1\nline2") == r'"line1\nline2"'

    def test_strips_carriage_return(self):
        result = lua_string("a\rb")
        assert "\r" not in result
        assert result == '"ab"'

    def test_complex_string(self):
        result = lua_string('He said "hello"\nand left')
        assert result == r'"He said \"hello\"\nand left"'

    def test_unicode_string(self):
        result = lua_string("Valdrakken")
        assert result == '"Valdrakken"'


# ======================================================================
# lua_number
# ======================================================================

class TestLuaNumber:
    def test_integer(self):
        assert lua_number(42) == "42"

    def test_zero(self):
        assert lua_number(0) == "0"

    def test_negative(self):
        assert lua_number(-5) == "-5"

    def test_none_returns_nil(self):
        assert lua_number(None) == "nil"

    def test_float_with_decimal(self):
        assert lua_number(3.5) == "3.5"

    def test_float_whole_number(self):
        """Float like 5.0 should be serialized as integer."""
        assert lua_number(5.0) == "5"

    def test_float_precision(self):
        assert lua_number(67.6) == "67.6"

    def test_large_integer(self):
        assert lua_number(123456) == "123456"


# ======================================================================
# lua_coords
# ======================================================================

class TestLuaCoords:
    def test_simple_coords(self):
        assert lua_coords([67.6, 72.8]) == "{67.6, 72.8}"

    def test_integer_coords(self):
        assert lua_coords([50, 50]) == "{50, 50}"

    def test_none_returns_nil(self):
        assert lua_coords(None) == "nil"

    def test_empty_list_returns_nil(self):
        assert lua_coords([]) == "nil"

    def test_single_element_returns_nil(self):
        assert lua_coords([5]) == "nil"

    def test_none_x_returns_nil(self):
        assert lua_coords([None, 50]) == "nil"

    def test_none_y_returns_nil(self):
        assert lua_coords([50, None]) == "nil"

    def test_three_elements_uses_first_two(self):
        result = lua_coords([10, 20, 30])
        assert result == "{10, 20}"

    def test_mixed_float_int(self):
        assert lua_coords([10.5, 20]) == "{10.5, 20}"


# ======================================================================
# lua_value
# ======================================================================

class TestLuaValue:
    def test_none(self):
        assert lua_value(None) == "nil"

    def test_true(self):
        assert lua_value(True) == "true"

    def test_false(self):
        assert lua_value(False) == "false"

    def test_integer(self):
        assert lua_value(42) == "42"

    def test_float(self):
        assert lua_value(3.5) == "3.5"

    def test_string(self):
        assert lua_value("hello") == '"hello"'

    def test_list_as_coords(self):
        assert lua_value([10, 20]) == "{10, 20}"

    def test_other_type_stringified(self):
        """Non-standard types get converted via str() then lua_string()."""
        result = lua_value({"a": 1})
        assert result.startswith('"')

    @pytest.mark.parametrize("val,expected", [
        (0, "0"),
        (True, "true"),
        (False, "false"),
        ("", '""'),
        (None, "nil"),
    ])
    def test_edge_values(self, val, expected):
        assert lua_value(val) == expected
