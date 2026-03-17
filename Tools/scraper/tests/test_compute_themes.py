"""Tests for compute_item_themes.py — theme scoring constants and logic."""

import re

import pytest

from compute_item_themes import (
    AESTHETIC_CONFLICTS,
    AESTHETIC_THEMES,
    ALWAYS_MERGE,
    CULTURE_THEMES,
    MERGE_TARGETS,
    MIN_SCORE_THRESHOLD,
    NAME_PATTERN_BOOSTS,
    NAME_PATTERNS,
    SACRED_EXCLUSIONS,
    TAG_TO_THEME,
    THEME_GROUPS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def name_matches_theme(name: str, target_theme: str) -> bool:
    """Return True if any NAME_PATTERN regex matches *name* for *target_theme*."""
    for pattern, theme in NAME_PATTERNS:
        if theme == target_theme and pattern.search(name):
            return True
    return False


def all_themes_for_name(name: str) -> set[str]:
    """Return all themes that match *name* via NAME_PATTERNS."""
    themes: set[str] = set()
    for pattern, theme in NAME_PATTERNS:
        if pattern.search(name):
            themes.add(theme)
    return themes


# ===========================================================================
# TAG_TO_THEME mapping
# ===========================================================================

class TestTagToTheme:
    """Validate the TAG_TO_THEME dictionary."""

    @pytest.mark.parametrize("tag, expected_theme", [
        ("blood elf", "Blood Elf"),
        ("night elf", "Night Elf"),
        ("nightborne", "Nightborne"),
        ("void elf", "Void Elf"),
        ("elven", "Elven"),
        ("human", "Human"),
        ("kul tiran", "Kul Tiran"),
        ("gilnean", "Gilnean"),
        ("gilnean / worgen", "Gilnean"),
        ("worgen", "Gilnean"),
        ("dwarven", "Dwarven"),
        ("dark iron dwarf", "Dark Iron"),
        ("gnomish", "Gnomish"),
        ("draenei", "Draenei"),
        ("orcish", "Orcish"),
        ("goblin", "Goblin"),
        ("tauren", "Tauren"),
        ("troll", "Troll"),
        ("zandalari troll", "Zandalari"),
        ("undead", "Undead"),
        ("pandaren", "Pandaren"),
        ("vulpera", "Vulpera"),
        ("dracthyr", "Dracthyr"),
        ("earthen", "Earthen"),
        ("haranir", "Haranir"),
        ("vrykul", "Vrykul"),
    ])
    def test_culture_tags(self, tag: str, expected_theme: str):
        assert TAG_TO_THEME[tag] == expected_theme

    @pytest.mark.parametrize("tag, expected_theme", [
        ("elegant", "Royal Court"),
        ("lavish", "Royal Court"),
        ("opulent", "Royal Court"),
        ("light", "Sacred Temple"),
        ("magical", "Arcane Sanctum"),
        ("nature", "Wild Garden"),
        ("spooky", "Haunted Manor"),
        ("dark", "Haunted Manor"),
        ("fae", "Enchanted Grove"),
        ("mechanical", "Tinker's Workshop"),
        ("void", "Void Rift"),
        ("fel", "Fel Forge"),
        ("simple", "Cottage Hearth"),
        ("casual", "Cottage Hearth"),
        ("folk", "Cottage Hearth"),
        ("bold", "Primal Camp"),
        ("pirate", "Seafarer's Haven"),
        ("rugged", "Primal Camp"),
        ("library", "Scholar's Archive"),
        ("kitchen", "Feast Hall"),
        ("dining room", "Feast Hall"),
        ("wine cellar", "Feast Hall"),
        ("trophy room", "War Room"),
    ])
    def test_aesthetic_tags(self, tag: str, expected_theme: str):
        assert TAG_TO_THEME[tag] == expected_theme

    def test_no_none_values(self):
        """Every tag must map to a non-None, non-empty theme string."""
        for tag, theme in TAG_TO_THEME.items():
            assert theme is not None, f"Tag '{tag}' maps to None"
            assert isinstance(theme, str) and len(theme) > 0, (
                f"Tag '{tag}' has invalid theme: {theme!r}"
            )

    def test_all_themes_are_recognized(self):
        """Every theme in TAG_TO_THEME must be in CULTURE_THEMES or AESTHETIC_THEMES."""
        all_known = CULTURE_THEMES | AESTHETIC_THEMES
        for tag, theme in TAG_TO_THEME.items():
            assert theme in all_known, (
                f"Tag '{tag}' maps to unknown theme '{theme}'"
            )

    def test_tags_are_lowercase(self):
        """All tag keys should be lowercase (wowdb tags are normalized)."""
        for tag in TAG_TO_THEME:
            assert tag == tag.lower(), f"Tag '{tag}' is not lowercase"


# ===========================================================================
# NAME_PATTERNS regex
# ===========================================================================

class TestNamePatterns:
    """Validate NAME_PATTERNS regex matching."""

    # --- Positive matches ---

    @pytest.mark.parametrize("name, expected_theme", [
        # Sacred Temple
        ("Sacred Altar", "Sacred Temple"),
        ("Holy Shrine of the Ancestors", "Sacred Temple"),
        ("Blessed Candle", "Sacred Temple"),
        ("Divine Chalice Stand", "Sacred Temple"),
        ("Cathedral Bell", "Sacred Temple"),
        ("Golden Censer", "Sacred Temple"),
        ("Naaru Crystal", "Sacred Temple"),
        ("Light-Infused Orb", "Sacred Temple"),
        ("Silver Hand Banner", "Sacred Temple"),
        ("Stained Glass Window", "Sacred Temple"),
        ("Replica Libram of Kings", "Sacred Temple"),
        ("Prayer Rug", "Sacred Temple"),
        ("Votive Candle Array", "Sacred Temple"),

        # Haunted Manor
        ("Haunted Candelabra", "Haunted Manor"),
        ("Spooky Pumpkin", "Haunted Manor"),
        ("Ornate Coffin", "Haunted Manor"),
        ("Old Cobweb Covered Shelf", "Haunted Manor"),
        ("Ancient Tombstone", "Haunted Manor"),
        ("Ghastly Lantern", "Haunted Manor"),
        ("Skull Pile", "Haunted Manor"),
        ("Crypt Wall Segment", "Haunted Manor"),

        # Scholar's Archive
        ("Dusty Book", "Scholar's Archive"),
        ("Ancient Tome", "Scholar's Archive"),
        ("Arcane Codex", "Scholar's Archive"),
        ("Worn Scroll", "Scholar's Archive"),
        ("Tall Bookshelf", "Scholar's Archive"),
        ("Quill and Inkwell", "Scholar's Archive"),
        ("Lectern Stand", "Scholar's Archive"),
        ("Royal Archive Shelf", "Scholar's Archive"),

        # Culture themes
        ("Gnomish Sprocket", "Gnomish"),
        ("Elven Tapestry", "Elven"),
        ("Orcish War Drum", "Orcish"),
        ("Dwarven Anvil", "Dwarven"),
        ("Tauren Totem", "Tauren"),
        ("Pandaren Lantern", "Pandaren"),
        ("Vrykul Shield", "Vrykul"),
        ("Haranir Runestone", "Haranir"),
        ("Draenei Crystal", "Draenei"),
        ("Gilnean Street Lamp", "Gilnean"),
        ("Night Elf Moonwell", "Night Elf"),
        ("Blood Elf Banner", "Blood Elf"),
        ("Dark Iron Forge", "Dark Iron"),
        ("Goblin Rocket", "Goblin"),
        ("Vulpera Tent", "Vulpera"),
        ("Undead Candelabra", "Undead"),
        ("Zandalari Gold Pot", "Troll"),  # Zandalari → Troll pattern

        # Elven language variants
        ("Kaldorei Canopy", "Elven"),
        ("Sin'dorei Banner", "Elven"),

        # Other aesthetics
        ("Arcane Sphere", "Arcane Sanctum"),
        ("Runic Circle", "Arcane Sanctum"),
        ("Ornate Gold Frame", "Royal Court"),
        ("Gilded Candelabra", "Royal Court"),
        ("Rustic Fence", "Cottage Hearth"),
        ("Verdant Vine", "Wild Garden"),
        ("Fae Lantern", "Enchanted Grove"),
        ("Void Crystal", "Void Rift"),
        ("Fel Candle", "Fel Forge"),
        ("Mechanical Squirrel Display", "Tinker's Workshop"),
        ("Clockwork Music Box", "Tinker's Workshop"),
        ("Pirate Flag", "Seafarer's Haven"),
        ("Tavern Sign", "Feast Hall"),
        ("Oak Barrel", "Feast Hall"),
        ("Iron Tankard", "Feast Hall"),
        ("Old Stove", "Feast Hall"),
        ("War Banner", "War Room"),
        ("Iron Weapon Rack", "War Room"),
        ("Training Dummy", "War Room"),
    ])
    def test_positive_match(self, name: str, expected_theme: str):
        assert name_matches_theme(name, expected_theme), (
            f"'{name}' should match '{expected_theme}' but did not"
        )

    # --- Negative matches (should NOT false-positive) ---

    @pytest.mark.parametrize("name, should_not_match", [
        ("Simple Wooden Chair", "Sacred Temple"),
        ("Red Curtain", "Haunted Manor"),
        ("Iron Lamp Post", "War Room"),  # "Iron" alone is not War Room
        ("Plush Carpet", "Scholar's Archive"),
        ("Stone Wall", "Dwarven"),
        ("Copper Pot", "Gnomish"),
        ("Woven Basket", "Pandaren"),
        ("Small Crate", "Seafarer's Haven"),
    ])
    def test_negative_match(self, name: str, should_not_match: str):
        assert not name_matches_theme(name, should_not_match), (
            f"'{name}' should NOT match '{should_not_match}' but did"
        )

    def test_patterns_are_valid_regex(self):
        """All NAME_PATTERNS entries should be compiled regex + string pairs."""
        for pattern, theme in NAME_PATTERNS:
            assert isinstance(pattern, re.Pattern), (
                f"Expected compiled pattern, got {type(pattern)}"
            )
            assert isinstance(theme, str) and len(theme) > 0

    def test_all_pattern_themes_are_known(self):
        """Every theme referenced in NAME_PATTERNS must be recognized."""
        all_known = CULTURE_THEMES | AESTHETIC_THEMES
        for _, theme in NAME_PATTERNS:
            assert theme in all_known, (
                f"NAME_PATTERNS references unknown theme '{theme}'"
            )


# ===========================================================================
# CULTURE_THEMES and AESTHETIC_THEMES
# ===========================================================================

class TestThemeSets:
    """Validate theme set structure and consistency."""

    def test_culture_themes_non_empty(self):
        assert len(CULTURE_THEMES) > 0

    def test_aesthetic_themes_non_empty(self):
        assert len(AESTHETIC_THEMES) > 0

    def test_no_overlap(self):
        """Culture and Aesthetic themes must be disjoint."""
        overlap = CULTURE_THEMES & AESTHETIC_THEMES
        assert len(overlap) == 0, f"Overlap: {overlap}"

    def test_theme_groups_covers_all(self):
        """THEME_GROUPS must have entries for every culture + aesthetic theme."""
        all_themes = CULTURE_THEMES | AESTHETIC_THEMES
        for theme in all_themes:
            assert theme in THEME_GROUPS, f"'{theme}' missing from THEME_GROUPS"

    def test_theme_groups_correct_labels(self):
        for theme in CULTURE_THEMES:
            assert THEME_GROUPS[theme] == "Culture"
        for theme in AESTHETIC_THEMES:
            assert THEME_GROUPS[theme] == "Aesthetic"

    def test_theme_names_are_nonempty_strings(self):
        for theme in CULTURE_THEMES | AESTHETIC_THEMES:
            assert isinstance(theme, str) and len(theme) > 0


# ===========================================================================
# Theme Merging Logic
# ===========================================================================

class TestMerging:
    """Validate MERGE_TARGETS and ALWAYS_MERGE."""

    @pytest.mark.parametrize("child, parent", [
        ("Blood Elf", "Elven"),
        ("Night Elf", "Elven"),
        ("Nightborne", "Elven"),
        ("Void Elf", "Elven"),
        ("Kul Tiran", "Human"),
        ("Gilnean", "Human"),
        ("Dark Iron", "Dwarven"),
        ("Zandalari", "Troll"),
        ("Earthen", "Dwarven"),
    ])
    def test_merge_targets(self, child: str, parent: str):
        assert MERGE_TARGETS[child] == parent

    def test_merge_targets_only_culture(self):
        """Merge targets should only involve culture themes."""
        for child, parent in MERGE_TARGETS.items():
            assert child in CULTURE_THEMES, f"'{child}' not a culture theme"
            assert parent in CULTURE_THEMES, f"'{parent}' not a culture theme"

    def test_merge_targets_no_cycles(self):
        """A merge target should not itself be a merge source."""
        for parent in MERGE_TARGETS.values():
            assert parent not in MERGE_TARGETS, (
                f"'{parent}' is both a merge target and a merge source"
            )

    @pytest.mark.parametrize("child, parent", [
        ("Kul Tiran", "Human"),
        ("Zandalari", "Troll"),
        ("Earthen", "Dwarven"),
    ])
    def test_always_merge(self, child: str, parent: str):
        assert ALWAYS_MERGE[child] == parent

    def test_always_merge_subset_of_merge_targets(self):
        """ALWAYS_MERGE must be a subset of MERGE_TARGETS."""
        for child, parent in ALWAYS_MERGE.items():
            assert child in MERGE_TARGETS, (
                f"ALWAYS_MERGE has '{child}' not in MERGE_TARGETS"
            )
            assert MERGE_TARGETS[child] == parent


# ===========================================================================
# Conflict Resolution
# ===========================================================================

class TestAestheticConflicts:
    """Validate AESTHETIC_CONFLICTS structure and conflict logic."""

    def test_all_keys_are_aesthetic(self):
        for theme in AESTHETIC_CONFLICTS:
            assert theme in AESTHETIC_THEMES, (
                f"Conflict key '{theme}' is not an aesthetic theme"
            )

    def test_all_values_are_aesthetic(self):
        for theme, conflicts in AESTHETIC_CONFLICTS.items():
            for c in conflicts:
                assert c in AESTHETIC_THEMES, (
                    f"'{c}' (conflict of '{theme}') is not an aesthetic theme"
                )

    def test_sacred_conflicts_with_haunted(self):
        assert "Haunted Manor" in AESTHETIC_CONFLICTS["Sacred Temple"]

    def test_sacred_conflicts_with_fel(self):
        assert "Fel Forge" in AESTHETIC_CONFLICTS["Sacred Temple"]

    def test_sacred_conflicts_with_void(self):
        assert "Void Rift" in AESTHETIC_CONFLICTS["Sacred Temple"]

    def test_royal_conflicts_with_cottage(self):
        assert "Cottage Hearth" in AESTHETIC_CONFLICTS["Royal Court"]

    def test_conflict_resolution_removes_unsupported(self):
        """If item has conflicting theme but NO name support, it should lose it.

        Simulate: item assigned Sacred Temple + name matches Haunted Manor keywords
        but NOT Sacred Temple keywords -> Sacred Temple should be removed.
        """
        item_themes = {"Sacred Temple": 80, "Haunted Manor": 60}
        name = "Spooky Coffin Lid"  # matches Haunted, not Sacred

        # Compute name-supported aesthetics
        name_aesthetics = all_themes_for_name(name)
        assert "Haunted Manor" in name_aesthetics
        assert "Sacred Temple" not in name_aesthetics

        # Apply conflict logic (mirrors compute_themes inner loop)
        themes_to_remove = []
        for theme in item_themes:
            if theme not in AESTHETIC_CONFLICTS:
                continue
            conflicting = AESTHETIC_CONFLICTS[theme] & name_aesthetics
            has_own_keywords = theme in name_aesthetics
            if conflicting and not has_own_keywords:
                themes_to_remove.append(theme)

        for theme in themes_to_remove:
            del item_themes[theme]

        assert "Sacred Temple" not in item_themes
        assert "Haunted Manor" in item_themes

    def test_conflict_keeps_dual_themed(self):
        """Item with name support for BOTH conflicting themes keeps both."""
        item_themes = {"Sacred Temple": 90, "Haunted Manor": 70}
        name = "Sacred Skull Shrine"  # matches both Sacred and Haunted

        name_aesthetics = all_themes_for_name(name)
        assert "Sacred Temple" in name_aesthetics
        assert "Haunted Manor" in name_aesthetics

        themes_to_remove = []
        for theme in item_themes:
            if theme not in AESTHETIC_CONFLICTS:
                continue
            conflicting = AESTHETIC_CONFLICTS[theme] & name_aesthetics
            has_own_keywords = theme in name_aesthetics
            if conflicting and not has_own_keywords:
                themes_to_remove.append(theme)

        assert len(themes_to_remove) == 0, "Should not remove any dual-themed entries"


# ===========================================================================
# Sacred Exclusions
# ===========================================================================

class TestSacredExclusions:
    """Validate SACRED_EXCLUSIONS prevent corrupt items from getting Sacred."""

    @pytest.mark.parametrize("name", [
        "Felblood Altar",
        "Corrupted Shrine",
        "Libram of the Dead",
        "Night Fae Altar",
    ])
    def test_excluded_names_match(self, name: str):
        assert any(ex.search(name) for ex in SACRED_EXCLUSIONS), (
            f"'{name}' should be caught by SACRED_EXCLUSIONS"
        )

    @pytest.mark.parametrize("name", [
        "Sacred Altar",
        "Golden Shrine",
        "Holy Libram",
    ])
    def test_clean_names_not_excluded(self, name: str):
        assert not any(ex.search(name) for ex in SACRED_EXCLUSIONS), (
            f"'{name}' should NOT be caught by SACRED_EXCLUSIONS"
        )


# ===========================================================================
# Name Pattern Boosts
# ===========================================================================

class TestNamePatternBoosts:
    """Validate NAME_PATTERN_BOOSTS structure."""

    def test_sacred_is_boosted(self):
        assert "Sacred Temple" in NAME_PATTERN_BOOSTS
        assert NAME_PATTERN_BOOSTS["Sacred Temple"] > 1.0

    def test_boost_themes_are_known(self):
        all_known = CULTURE_THEMES | AESTHETIC_THEMES
        for theme in NAME_PATTERN_BOOSTS:
            assert theme in all_known, (
                f"Boosted theme '{theme}' not recognized"
            )


# ===========================================================================
# MIN_SCORE_THRESHOLD
# ===========================================================================

class TestScoreThreshold:
    def test_threshold_in_range(self):
        assert 0 < MIN_SCORE_THRESHOLD < 100
