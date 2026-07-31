"""Fixture test for gen_code_map.py — exercises init, check, drift, and errors.

Builds a small two-package project in a temporary directory and drives the
generator through its lifecycle: ``--init`` bootstraps, ``--check`` passes
clean, a docstring edit makes ``--check`` fail, regeneration restores clean
while preserving hand-written narrative text, and damaged input exits 2.

Stdlib-only, like the script under test. Run with:

    python tools/test_gen_code_map.py
"""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gen_code_map as gcm  # noqa: E402

ALPHA_PARSER = '''"""Widget parser module."""


def parse_widget(raw: str, strict: bool = True) -> dict:
    """Parse one raw widget line into a record."""
    return {}
'''

BETA_EMIT = '''"""Report emitter."""


def emit_report(records: list) -> str:
    """Render records to a markdown report."""
    return ""
'''


def build_fixture(root: Path) -> None:
    """Create the two-package fixture project under ``root``."""
    acme = root / "src" / "acme"
    (acme / "alpha").mkdir(parents=True)
    (acme / "beta").mkdir(parents=True)
    (acme / "__init__.py").write_text('"""acme — fixture project."""\n')
    (acme / "alpha" / "__init__.py").write_text(
        '"""Alpha package — parses widgets into records."""\n'
    )
    (acme / "alpha" / "parser.py").write_text(ALPHA_PARSER)
    (acme / "beta" / "__init__.py").write_text(
        '"""Beta package — emits reports from parsed records."""\n'
    )
    (acme / "beta" / "emit.py").write_text(BETA_EMIT)


def run(args: list[str], root: Path) -> int:
    """Invoke the generator's main() with stderr captured."""
    with contextlib.redirect_stderr(io.StringIO()):
        return gcm.main(["--src-root", "src/acme", *args], repo_root=root)


class GenCodeMapTest(unittest.TestCase):
    """Lifecycle tests against a temporary fixture project."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        build_fixture(self.root)

    def test_init_bootstraps_readmes_and_code_map(self) -> None:
        """--init creates package READMEs and the code map with extracted content."""
        self.assertEqual(run(["--init"], self.root), 0)
        alpha = (self.root / "src/acme/alpha/README.md").read_text()
        self.assertIn(gcm.PKG_BEGIN, alpha)
        self.assertIn("parse_widget(raw: str, strict: bool=True) -> dict", alpha)
        self.assertIn("Parse one raw widget line into a record.", alpha)
        code_map = (self.root / "docs/code_map.md").read_text()
        self.assertIn("Alpha package — parses widgets into records.", code_map)
        self.assertIn("`acme.beta`", code_map)

    def test_check_clean_after_init(self) -> None:
        """--check exits 0 immediately after --init."""
        run(["--init"], self.root)
        self.assertEqual(run(["--check"], self.root), 0)

    def test_check_detects_docstring_drift(self) -> None:
        """--check exits 1 after a docstring changes without regeneration."""
        run(["--init"], self.root)
        parser = self.root / "src/acme/alpha/parser.py"
        parser.write_text(parser.read_text().replace("one raw widget", "a widget"))
        self.assertEqual(run(["--check"], self.root), 1)

    def test_regenerate_restores_clean_and_preserves_narrative(self) -> None:
        """Regeneration clears drift; hand-written text outside markers survives."""
        run(["--init"], self.root)
        readme = self.root / "src/acme/alpha/README.md"
        readme.write_text(
            readme.read_text().replace(
                "_TODO_: one-sentence elevator pitch.", "Parses widgets. Hand-written."
            )
        )
        parser = self.root / "src/acme/alpha/parser.py"
        parser.write_text(parser.read_text().replace("one raw widget", "a widget"))
        self.assertEqual(run([], self.root), 0)
        self.assertEqual(run(["--check"], self.root), 0)
        self.assertIn("Parses widgets. Hand-written.", readme.read_text())

    def test_missing_readme_without_init_is_config_error(self) -> None:
        """A package without a README exits 2 unless --init is given."""
        self.assertEqual(run([], self.root), 2)

    def test_damaged_marker_is_config_error(self) -> None:
        """A README missing its END marker exits 2 rather than guessing."""
        run(["--init"], self.root)
        readme = self.root / "src/acme/alpha/README.md"
        readme.write_text(readme.read_text().replace(gcm.PKG_END, ""))
        self.assertEqual(run([], self.root), 2)


FLAT_STORE = '''"""Storage helpers."""


def save_record(rec: dict) -> None:
    """Persist one record."""
'''


def build_flat_fixture(root: Path) -> None:
    """Create a single-package, no-subpackage fixture project under ``root``."""
    api = root / "api"
    api.mkdir(parents=True)
    (api / "__init__.py").write_text('"""api — flat fixture service."""\n')
    (api / "store.py").write_text(FLAT_STORE)


def run_flat(args: list[str], root: Path) -> int:
    """Invoke main() against the flat fixture with stderr captured."""
    with contextlib.redirect_stderr(io.StringIO()):
        return gcm.main(["--src-root", "api", *args], repo_root=root)


class FlatPackageTest(unittest.TestCase):
    """--flat: the source root is itself the only package."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        build_flat_fixture(self.root)

    def test_init_documents_the_root_package_itself(self) -> None:
        """--flat --init writes the root package's own README from its flat modules."""
        self.assertEqual(run_flat(["--flat", "--init"], self.root), 0)
        readme = (self.root / "api/README.md").read_text()
        self.assertIn(gcm.PKG_BEGIN, readme)
        self.assertIn("save_record(rec: dict) -> None", readme)
        self.assertIn("Persist one record.", readme)
        self.assertIn("# api", readme)

    def test_code_map_indexes_the_single_package(self) -> None:
        """The index is one row: the root package, its role, and a link that resolves."""
        run_flat(["--flat", "--init"], self.root)
        code_map = (self.root / "docs/code_map.md").read_text()
        self.assertIn("`api`", code_map)
        self.assertIn("api — flat fixture service.", code_map)
        self.assertIn("../api/README.md", code_map)
        self.assertNotIn("{pkg}", code_map)
        # The link is relative to docs/ — confirm it resolves to the real file.
        self.assertTrue((self.root / "docs" / "../api/README.md").resolve().is_file())

    def test_check_clean_then_detects_drift(self) -> None:
        """--check passes after init and fails once a docstring changes."""
        run_flat(["--flat", "--init"], self.root)
        self.assertEqual(run_flat(["--flat", "--check"], self.root), 0)
        store = self.root / "api/store.py"
        store.write_text(store.read_text().replace("Persist one", "Store one"))
        self.assertEqual(run_flat(["--flat", "--check"], self.root), 1)
        self.assertEqual(run_flat(["--flat"], self.root), 0)
        self.assertEqual(run_flat(["--flat", "--check"], self.root), 0)

    def test_without_flat_the_layout_yields_no_packages(self) -> None:
        """Regression: default discovery finds nothing here — the flag is load-bearing."""
        self.assertEqual(gcm.discover_subpackages(self.root / "api"), [])
        run_flat(["--init"], self.root)
        self.assertFalse((self.root / "api/README.md").is_file())


if __name__ == "__main__":
    unittest.main()
