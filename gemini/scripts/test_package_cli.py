"""Regression checks for release preservation and self-contained archives."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile
from package_cli import package


class ReleasePackagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / "extension"
        self.source.mkdir()
        (self.source / "gemini-extension.json").write_text(json.dumps({"version": "0.1.0"}))
        (self.source / "GEMINI.md").write_text("Expected context")
        self.output = self.base / "release.zip"
        self.previous = b"Previous valid release"
        self.output.write_bytes(self.previous)

    def test_in_source_output_is_rejected_before_creating_a_file(self):
        target = self.source / "release.zip"
        with self.assertRaises(ValueError):
            package(self.source, target)
        self.assertFalse(target.exists())

    def test_source_file_cannot_be_overwritten_as_output(self):
        target = self.source / "GEMINI.md"
        before = target.read_bytes()
        with self.assertRaises(ValueError):
            package(self.source, target)
        self.assertEqual(target.read_bytes(), before)

    def test_rejected_hidden_input_preserves_previous_release(self):
        (self.source / ".env").write_text("Synthetic fixture")
        with self.assertRaises(ValueError):
            package(self.source, self.output)
        self.assertEqual(self.output.read_bytes(), self.previous)

    def test_missing_manifest_version_preserves_previous_release(self):
        (self.source / "gemini-extension.json").write_text("{}")
        with self.assertRaises(ValueError):
            package(self.source, self.output)
        self.assertEqual(self.output.read_bytes(), self.previous)

    def test_archive_write_failure_preserves_release_and_removes_staging(self):
        with patch.object(ZipFile, "writestr", side_effect=OSError("Synthetic disk failure")):
            with self.assertRaises(OSError):
                package(self.source, self.output)
        self.assertEqual(self.output.read_bytes(), self.previous)
        self.assertFalse(list(self.base.glob(".release.zip.*.tmp")))

    def test_linked_directory_is_rejected_without_losing_release(self):
        outside = self.base / "external"
        outside.mkdir()
        (outside / "private.txt").write_text("Synthetic fixture")
        try:
            (self.source / "linked").symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("Symlink creation unavailable on this host")
        with self.assertRaises(ValueError):
            package(self.source, self.output)
        self.assertEqual(self.output.read_bytes(), self.previous)

    def test_archive_is_deterministic_and_has_only_source_members_at_root(self):
        first = package(self.source, self.output)
        other = self.base / "second.zip"
        self.assertEqual(first, package(self.source, other))
        self.assertEqual(self.output.read_bytes(), other.read_bytes())
        with ZipFile(self.output) as archive:
            self.assertEqual(archive.namelist(), ["gemini-extension.json", "GEMINI.md"])
            self.assertTrue(all(info.create_system == 0 for info in archive.infolist()))
            self.assertEqual(archive.read("GEMINI.md"), b"Expected context")


if __name__ == "__main__":
    unittest.main()
