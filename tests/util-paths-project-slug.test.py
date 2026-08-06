#!/usr/bin/env python3
"""project_slug() resolves the project dir Claude Code ACTUALLY created.

Regression guard for sonichi#2723: three inline slug formulas, each mapping
only `/`, silently named directories Claude Code never writes to on any
install whose path contains a space or a dot — i.e. every macOS app-bundle
install under `~/Library/Application Support/`. Nothing errored; the wrong
directory was simply created on first write and memory split across parallel
stores.

The fix is discovery-first: find the existing project dir describing the same
path (compared through a derivation-independent key) instead of predicting its
name. These tests pin that behavior, including the case a formula CANNOT get
right — a slug whose characters no current formula reproduces.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from util_paths import (  # noqa: E402
    _slug_formula,
    project_slug,
    slug_derivation_key,
)

BUNDLE = "/Users/me/Library/Application Support/space.ag2.app/engine/sutando"
BUNDLE_TRUE_SLUG = "-Users-me-Library-Application-Support-space-ag2-app-engine-sutando"

#: What the pre-fix code computed, at all three call sites.
def _legacy_formula(path: str) -> str:
    return str(path).replace("/", "-")


class SlugDerivationKeyTest(unittest.TestCase):
    def test_divergent_derivations_of_one_path_share_a_key(self):
        """The three slugs one repo produced on the reporting machine agree."""
        variants = [
            BUNDLE_TRUE_SLUG,
            "-Users-me-Library-Application Support-space.ag2.app-engine-sutando",
            "-Users-me-Library-Application-Support-space.ag2.app-engine-sutando",
        ]
        keys = {slug_derivation_key(v) for v in variants}
        self.assertEqual(len(keys), 1, f"variants disagreed: {keys}")

    def test_unrelated_projects_do_not_collide(self):
        self.assertNotEqual(
            slug_derivation_key(BUNDLE_TRUE_SLUG),
            slug_derivation_key("-Users-me-Documents-unrelated-repo"),
        )


class SlugFormulaTest(unittest.TestCase):
    def test_legacy_formula_was_wrong_for_bundle_paths(self):
        """The bug, pinned: `/`-only mapping names a dir that never exists."""
        self.assertNotEqual(_legacy_formula(BUNDLE), BUNDLE_TRUE_SLUG)

    def test_formula_reproduces_the_bundle_slug(self):
        """Fallback is right for the install class that motivated the fix."""
        self.assertEqual(_slug_formula(BUNDLE), BUNDLE_TRUE_SLUG)

    def test_formula_is_unchanged_for_plain_paths(self):
        """Installs that already worked keep working — strict superset."""
        plain = "/Users/me/workspace/sutando"
        self.assertEqual(_slug_formula(plain), _legacy_formula(plain))


class ProjectSlugDiscoveryTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        (self.home / "projects").mkdir()
        self._prev = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.home)

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = self._prev
        self._tmp.cleanup()

    def _mkproject(self, name: str) -> Path:
        d = self.home / "projects" / name
        d.mkdir()
        return d

    def test_discovers_the_real_dir_over_the_formula(self):
        """A slug the formula does NOT reproduce is still found.

        This is the case that makes discovery worth the code: Claude Code has
        already changed how it maps `_` (one machine holds both
        `...0nw4fhvs4599_zcgpk3fbcnh0000gn...` and `...4599-zcgpk3...`), so
        the formula is a prediction that expires. Discovery does not.
        """
        real = "-Users-me-Library-Application_Support-space-ag2-app-engine-sutando"
        self._mkproject(real)
        self.assertNotEqual(_slug_formula(BUNDLE), real)  # formula cannot get here
        self.assertEqual(project_slug(BUNDLE), real)

    def test_falls_back_to_formula_when_nothing_exists(self):
        """First run: no dir yet, so any answer is a guess — use the formula."""
        self.assertEqual(project_slug(BUNDLE), _slug_formula(BUNDLE))

    def test_ignores_unrelated_projects(self):
        self._mkproject("-Users-me-Documents-unrelated-repo")
        self.assertEqual(project_slug(BUNDLE), _slug_formula(BUNDLE))

    def test_prefers_most_recently_modified_on_a_split(self):
        """The split this function exists to stop: pick the live store."""
        stale = self._mkproject(
            "-Users-me-Library-Application Support-space.ag2.app-engine-sutando"
        )
        live = self._mkproject(BUNDLE_TRUE_SLUG)
        os.utime(stale, (1_000_000, 1_000_000))
        os.utime(live, (2_000_000, 2_000_000))
        self.assertEqual(project_slug(BUNDLE), BUNDLE_TRUE_SLUG)

    def test_missing_projects_dir_is_not_an_error(self):
        for child in (self.home / "projects").iterdir():
            child.rmdir()
        (self.home / "projects").rmdir()
        self.assertEqual(project_slug(BUNDLE), _slug_formula(BUNDLE))


if __name__ == "__main__":
    unittest.main()
