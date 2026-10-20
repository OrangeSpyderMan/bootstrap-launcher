"""Execute the embedded installer against synthetic official-release responses."""
import hashlib
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class UpstreamGitHubCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="upstream-gh-test-")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        code = (ROOT / "bootstrap.sh").read_text().split("<<'GH_PY'\n", 1)[1].split("\nGH_PY", 1)[0]
        self.module = {"__name__": "upstream_gh_test"}
        exec(compile(code, "bootstrap.sh:upstream-gh", "exec"), self.module)
        self.arch = patch.object(self.module["platform"], "machine", return_value="x86_64")
        self.arch.start()
        self.addCleanup(self.arch.stop)
        self.binary_content = b"#!/bin/sh\necho fixture-gh\n"
        self.version = "9.8.7"
        self.name = "gh_9.8.7_linux_amd64.tar.gz"
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w:gz") as stream:
            member = tarfile.TarInfo("gh_9.8.7_linux_amd64/bin/gh")
            member.size = len(self.binary_content)
            stream.addfile(member, io.BytesIO(self.binary_content))
        self.archive = archive.getvalue()
        self.checksums = (hashlib.sha256(self.archive).hexdigest() + "  " + self.name + "\n").encode()
        self.release = {"tag_name": "v9.8.7", "draft": False, "prerelease": False,
                        "assets": [{"name": name, "browser_download_url":
                                    "https://github.com/cli/cli/releases/download/v9.8.7/" + name}
                                   for name in [self.name, "gh_9.8.7_checksums.txt"]]}
        self.requests = []
        self.module["fetch"] = self.fetch

    def fetch(self, url, limit=100 * 1024 * 1024):
        self.requests.append(url)
        if url.endswith(".tar.gz"):
            return self.archive
        if url.endswith("checksums.txt"):
            return self.checksums
        return json.dumps(self.release).encode()

    def ensure(self, requested="latest", authorized=True):
        return self.module["ensure_gh"](self.home, requested, authorized, True)

    def test_checksum_verified_user_local_install_and_cache_reuse(self):
        path = self.ensure()
        self.assertEqual(path.read_bytes(), self.binary_content)
        self.assertTrue(os.access(path, os.X_OK))
        self.assertTrue(path.is_relative_to(self.home / ".local/lib/bootstrap-launcher/gh"))
        self.requests.clear()
        self.assertEqual(self.ensure(authorized=False), path)
        self.assertEqual(len(self.requests), 1)  # Resolve latest; don't download again.
        self.assertTrue(self.requests[0].endswith("/latest"))

    def test_pinned_version_uses_matching_official_tag(self):
        self.ensure(requested="9.8.7")
        self.assertTrue(self.requests[0].endswith("/tags/v9.8.7"))

    def test_bad_checksum_installs_nothing(self):
        self.checksums = ("0" * 64 + "  " + self.name + "\n").encode()
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            self.ensure()
        self.assertFalse((self.home / ".local").exists())

    def test_missing_confirmation_does_not_download_binary(self):
        with self.assertRaisesRegex(ValueError, "--install-prerequisites"):
            self.ensure(authorized=False)
        self.assertEqual(len(self.requests), 1)
        self.assertFalse((self.home / ".local").exists())

    def test_prereleases_and_unsupported_architectures_fail_closed(self):
        self.release["prerelease"] = True
        with self.assertRaisesRegex(ValueError, "stable"):
            self.ensure()
        self.release["prerelease"] = False
        self.module["platform"].machine = lambda: "unknown-cpu"
        with self.assertRaisesRegex(ValueError, "CPU"):
            self.ensure()

    def test_changed_cached_binary_requires_reinstallation(self):
        path = self.ensure()
        path.write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "--install-prerequisites"):
            self.ensure(authorized=False)
        self.ensure()
        self.assertEqual(path.read_bytes(), self.binary_content)

    def test_cache_symlink_is_refused(self):
        (self.home / ".local").symlink_to(self.home / "elsewhere")
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.ensure()

    def test_missing_binary_asset_fails_without_guessing_other_arch(self):
        self.release["assets"] = [item for item in self.release["assets"] if item["name"].endswith(".txt")]
        with self.assertRaisesRegex(ValueError, "matching binary"):
            self.ensure()


if __name__ == "__main__":
    unittest.main()
