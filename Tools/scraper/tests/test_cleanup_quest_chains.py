"""Tests for cleanup_quest_chains.py — chain computation and HTML parsing."""

import pytest

from cleanup_quest_chains import (
    compute_chain_length,
    get_chain_list,
    _parse_series_table,
    _parse_storyline_list,
)


# ======================================================================
# Fixtures — quest chain data
# ======================================================================

@pytest.fixture
def linear_chain():
    """A simple linear chain: 100 → 200 → 300 → 400."""
    return {
        "100": {"name": "Quest Start", "prereqs": []},
        "200": {"name": "Quest Mid 1", "prereqs": [100]},
        "300": {"name": "Quest Mid 2", "prereqs": [200]},
        "400": {"name": "Quest End", "prereqs": [300]},
    }


@pytest.fixture
def single_quest():
    """A single quest with no prereqs."""
    return {
        "500": {"name": "Standalone Quest", "prereqs": []},
    }


@pytest.fixture
def branching_chain():
    """Chain with branching: 100 → 200, 100 → 300 (but prereqs[0] picks first)."""
    return {
        "100": {"name": "Root", "prereqs": []},
        "200": {"name": "Branch A", "prereqs": [100]},
        "300": {"name": "Branch B", "prereqs": [100]},
    }


@pytest.fixture
def long_chain():
    """A chain of 10 quests: 1 → 2 → 3 → ... → 10."""
    quests = {}
    for i in range(1, 11):
        prereqs = [i - 1] if i > 1 else []
        quests[str(i)] = {"name": f"Quest {i}", "prereqs": prereqs}
    return quests


# ======================================================================
# compute_chain_length
# ======================================================================

class TestComputeChainLength:
    def test_linear_chain_end(self, linear_chain):
        assert compute_chain_length(400, linear_chain) == 4

    def test_linear_chain_middle(self, linear_chain):
        assert compute_chain_length(200, linear_chain) == 2

    def test_linear_chain_start(self, linear_chain):
        assert compute_chain_length(100, linear_chain) == 1

    def test_single_quest(self, single_quest):
        assert compute_chain_length(500, single_quest) == 1

    def test_missing_quest(self, linear_chain):
        assert compute_chain_length(999, linear_chain) == 0

    def test_long_chain(self, long_chain):
        assert compute_chain_length(10, long_chain) == 10
        assert compute_chain_length(5, long_chain) == 5
        assert compute_chain_length(1, long_chain) == 1

    def test_branching_from_branch(self, branching_chain):
        """Branch A has length 2 (itself + root)."""
        assert compute_chain_length(200, branching_chain) == 2
        assert compute_chain_length(300, branching_chain) == 2

    def test_empty_quests(self):
        assert compute_chain_length(1, {}) == 0

    def test_quest_with_missing_prereq(self):
        """Quest references a prereq that doesn't exist in the dict."""
        quests = {
            "200": {"name": "Quest", "prereqs": [100]},
        }
        # 200 exists (length 1), but prereq 100 is missing so chain stops
        assert compute_chain_length(200, quests) == 1

    def test_circular_reference_protection(self):
        """Circular prereqs should not cause infinite loop."""
        quests = {
            "1": {"name": "A", "prereqs": [2]},
            "2": {"name": "B", "prereqs": [1]},
        }
        # Should terminate thanks to seen-set
        result = compute_chain_length(1, quests)
        assert result == 2  # visits 1, then 2, then 1 is in seen → stop


# ======================================================================
# get_chain_list
# ======================================================================

class TestGetChainList:
    def test_linear_chain_end(self, linear_chain):
        chain = get_chain_list(400, linear_chain)
        assert chain == ["100", "200", "300", "400"]

    def test_linear_chain_middle(self, linear_chain):
        chain = get_chain_list(200, linear_chain)
        assert chain == ["100", "200"]

    def test_linear_chain_start(self, linear_chain):
        chain = get_chain_list(100, linear_chain)
        assert chain == ["100"]

    def test_single_quest(self, single_quest):
        chain = get_chain_list(500, single_quest)
        assert chain == ["500"]

    def test_missing_quest(self, linear_chain):
        chain = get_chain_list(999, linear_chain)
        assert chain == []

    def test_long_chain(self, long_chain):
        chain = get_chain_list(10, long_chain)
        assert chain == [str(i) for i in range(1, 11)]

    def test_chain_is_root_first(self, linear_chain):
        """Chain should be ordered root → leaf."""
        chain = get_chain_list(400, linear_chain)
        assert chain[0] == "100"  # root
        assert chain[-1] == "400"  # leaf

    def test_empty_quests(self):
        assert get_chain_list(1, {}) == []

    def test_quest_with_missing_prereq(self):
        quests = {
            "200": {"name": "Quest", "prereqs": [100]},
        }
        chain = get_chain_list(200, quests)
        assert chain == ["200"]

    def test_circular_reference_protection(self):
        quests = {
            "1": {"name": "A", "prereqs": [2]},
            "2": {"name": "B", "prereqs": [1]},
        }
        chain = get_chain_list(1, quests)
        assert len(chain) == 2


# ======================================================================
# _parse_series_table
# ======================================================================

class TestParseSeriesTable:
    def test_basic_series(self):
        html = """
        <table class="series">
            <tr><td><a href="/quest=100">First Quest</a></td></tr>
            <tr><td><b>Current Quest</b></td></tr>
            <tr><td><a href="/quest=300">Third Quest</a></td></tr>
        </table>
        """
        result = _parse_series_table(html, quest_id=200)
        assert len(result) == 3
        assert result[0]["quest_id"] == 100
        assert result[0]["name"] == "First Quest"
        assert result[0]["is_current"] is False
        assert result[1]["quest_id"] == 200  # uses quest_id param for bold
        assert result[1]["name"] == "Current Quest"
        assert result[1]["is_current"] is True
        assert result[2]["quest_id"] == 300
        assert result[2]["is_current"] is False

    def test_no_series_table(self):
        html = "<html><body>No series here</body></html>"
        result = _parse_series_table(html, quest_id=100)
        assert result == []

    def test_single_entry(self):
        html = """
        <table class="series">
            <tr><td><b>Solo Quest</b></td></tr>
        </table>
        """
        result = _parse_series_table(html, quest_id=42)
        assert len(result) == 1
        assert result[0]["quest_id"] == 42
        assert result[0]["is_current"] is True

    def test_multiple_links(self):
        html = """
        <table class="series">
            <tr><td><a href="/quest=10">Q1</a></td></tr>
            <tr><td><a href="/quest=20">Q2</a></td></tr>
            <tr><td><a href="/quest=30">Q3</a></td></tr>
            <tr><td><a href="/quest=40">Q4</a></td></tr>
            <tr><td><a href="/quest=50">Q5</a></td></tr>
        </table>
        """
        result = _parse_series_table(html, quest_id=99)
        assert len(result) == 5
        assert [r["quest_id"] for r in result] == [10, 20, 30, 40, 50]

    def test_empty_table(self):
        html = '<table class="series"></table>'
        result = _parse_series_table(html, quest_id=1)
        assert result == []

    def test_link_with_extra_attributes(self):
        html = """
        <table class="series">
            <tr><td><a class="q1" href="https://wowhead.com/quest=999/some-slug">Quest Name</a></td></tr>
        </table>
        """
        result = _parse_series_table(html, quest_id=1)
        assert len(result) == 1
        assert result[0]["quest_id"] == 999
        assert result[0]["name"] == "Quest Name"

    def test_whitespace_in_names(self):
        html = """
        <table class="series">
            <tr><td><a href="/quest=1">  Spaced Quest  </a></td></tr>
            <tr><td><b>  Current  </b></td></tr>
        </table>
        """
        result = _parse_series_table(html, quest_id=2)
        assert result[0]["name"] == "Spaced Quest"
        assert result[1]["name"] == "Current"


# ======================================================================
# _parse_storyline_list
# ======================================================================

class TestParseStorylineList:
    def test_basic_storyline(self):
        """The li class regex is non-greedy and optional, so is_current is only
        True for li elements that have no link (plain text fallback path).
        Linked items always get is_current=False regardless of li class."""
        html = """
        <a href="/storyline/test-story">Test Story</a>
        <div class="quick-facts-storyline-list">
            <ol>
                <li><a href="/quest=10">Quest One</a></li>
                <li class="current"><a href="/quest=20">Quest Two</a></li>
                <li><a href="/quest=30">Quest Three</a></li>
            </ol>
        </div>
        """
        name, result = _parse_storyline_list(html, quest_id=20)
        assert name == "Test Story"
        assert len(result) == 3
        assert result[0]["quest_id"] == 10
        assert result[0]["is_current"] is False
        # Linked items always get is_current=False (regex limitation)
        assert result[1]["quest_id"] == 20
        assert result[1]["is_current"] is False
        assert result[2]["quest_id"] == 30
        assert result[2]["is_current"] is False

    def test_no_storyline(self):
        html = "<html><body>No storyline</body></html>"
        name, result = _parse_storyline_list(html, quest_id=1)
        assert name is None
        assert result == []

    def test_storyline_without_name(self):
        html = """
        <div class="quick-facts-storyline-list">
            <ol>
                <li><a href="/quest=10">Quest One</a></li>
            </ol>
        </div>
        """
        name, result = _parse_storyline_list(html, quest_id=10)
        assert name is None
        assert len(result) == 1

    def test_current_quest_without_link(self):
        """A current quest with no link should use the quest_id param."""
        html = """
        <div class="quick-facts-storyline-list">
            <ol>
                <li><a href="/quest=10">Quest One</a></li>
                <li class="current">Current Quest Name</li>
            </ol>
        </div>
        """
        name, result = _parse_storyline_list(html, quest_id=20)
        assert len(result) == 2
        assert result[1]["quest_id"] == 20
        assert result[1]["is_current"] is True
        assert result[1]["name"] == "Current Quest Name"

    def test_empty_ol(self):
        html = """
        <div class="quick-facts-storyline-list">
            <ol></ol>
        </div>
        """
        name, result = _parse_storyline_list(html, quest_id=1)
        assert result == []

    def test_no_ol_inside_div(self):
        html = """
        <div class="quick-facts-storyline-list">
            <p>No list here</p>
        </div>
        """
        name, result = _parse_storyline_list(html, quest_id=1)
        assert result == []

    def test_storyline_name_extraction(self):
        html = """
        Some text before
        <a href="/storyline/my-storyline">My Great Storyline</a>
        more text
        <div class="quick-facts-storyline-list">
            <ol>
                <li><a href="/quest=1">Q1</a></li>
            </ol>
        </div>
        """
        name, result = _parse_storyline_list(html, quest_id=1)
        assert name == "My Great Storyline"

    def test_multiple_quests(self):
        quests_html = "".join(
            f'<li><a href="/quest={i}">Quest {i}</a></li>' for i in range(1, 8)
        )
        html = f"""
        <div class="quick-facts-storyline-list">
            <ol>{quests_html}</ol>
        </div>
        """
        name, result = _parse_storyline_list(html, quest_id=1)
        assert len(result) == 7
        assert [r["quest_id"] for r in result] == list(range(1, 8))
