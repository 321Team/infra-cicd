"""Tests for fail-closed apko artifact intake and schema-v3 config materialization."""
import copy
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import build_scan as flow
import materialize_app_build_scan_v3 as tool


SOURCE_SHA = "1" * 40
TOOL_SHA = "2" * 40


def identity(role):
    return {
        "format": "docker-archive",
        "archive_sha256": ("3" if role == "builder" else "4") * 64,
        "manifest_json_sha256": ("5" if role == "builder" else "6") * 64,
        "index_json_sha256": ("7" if role == "builder" else "8") * 64,
        "config_digest": "sha256:" + (("9" if role == "builder" else "a") * 64),
        "diff_ids": ["sha256:" + (("b" if role == "builder" else "c") * 64)],
        "layer_count": 1,
        "repo_tag": "index.docker.io/library/fds-apko-wolfi-" + role + ":" + SOURCE_SHA + "-amd64",
        "platform": "linux/amd64",
    }


def manifest():
    profiles = {}
    for name in ("transaction-api-runtime", "transaction-api-test",
                 "fds-engine-runtime", "fds-engine-test"):
        profiles[name] = {
            "roots_sha256": "d" * 64,
            "lock_sha256": "e" * 64,
            "inventory_sha256": "f" * 64,
        }
    return {
        "schema": "fds.apko-offline-supply.v1",
        "source": {"repository": "321Team/fds-msa", "commit": SOURCE_SHA},
        "supply_tool": {"repository": "321Team/fds-msa", "commit": TOOL_SHA},
        "platform": "linux/amd64",
        "apko": {"version": "1.4.4", "release_archive_sha256": "0" * 64},
        "images": {"builder": identity("builder"), "runtime": identity("runtime")},
        "profiles": profiles,
        "requirements": {
            "transaction-api/requirements.txt": "1" * 64,
            "transaction-api/requirements-test.txt": "2" * 64,
            "transaction-api/requirements-runtime.lock": "e" * 64,
            "transaction-api/requirements-test.lock": "e" * 64,
            "fds-engine/requirements.txt": "3" * 64,
            "fds-engine/requirements-test.txt": "4" * 64,
            "fds-engine/requirements-runtime.lock": "e" * 64,
            "fds-engine/requirements-test.lock": "e" * 64,
        },
        "apko_inputs": {
            name: str(index) * 64
            for index, name in enumerate(tool.SOURCE_INPUT_MAP.values(), 1)
        },
        "policy": {
            "delivery_authorization": False,
            "harbor_push": "NOT_EXECUTED",
            "high_critical_exception": False,
            "kubernetes_deploy": "NOT_EXECUTED",
            "purpose": "ON_PREM_TEST_BUILD_SCAN_SUPPLY_ONLY",
        },
    }


def approved_scope():
    return {
        "schema": "P1-CICD-test-scope-proposal-1",
        "status": "APPROVED",
        "source_commit": SOURCE_SHA,
        "approval": {
            "approved_by": "fixture reviewer",
            "approval_reference": "https://github.com/fixture/review/1",
        },
        "services": {
            "transaction-api": {
                "unit": {"status": "NOT_APPLICABLE", "reason": "fixture approved"},
                "contract": {
                    "cwd": "transaction-api",
                    "argv": [".venv/bin/python", "-m", "pytest", "-q", "tests/test_health.py"],
                    "timeout_seconds": 300,
                },
            },
            "fds-engine": {
                "unit": {
                    "cwd": "fds-engine",
                    "argv": [".venv/bin/python", "-m", "pytest", "-q", "tests/test_rules.py"],
                    "timeout_seconds": 300,
                },
                "contract": {
                    "cwd": "fds-engine",
                    "argv": [".venv/bin/python", "-m", "pytest", "-q", "tests/test_health.py"],
                    "timeout_seconds": 300,
                },
            },
        },
    }


class MaterializeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.parent = self.root / "home"
        self.parent.mkdir()
        self.handoff_path = self.root / "handoff.json"
        self.scope_path = self.root / "scope.json"
        self.artifact = self.root / "artifact.zip"
        self.output = self.root / "out.json"

    def build_artifact_and_handoff(self):
        manifest_raw = (json.dumps(manifest(), sort_keys=True, indent=2) + "\n").encode()
        payloads = {
            "SUPPLY_MANIFEST.json": manifest_raw,
            "builder.oci.tar": b"builder",
            "runtime.oci.tar": b"runtime",
            "inputs/wolfi-python313-builder.apko.yaml": b"a",
            "inputs/wolfi-python313-builder.apko.lock.json": b"b",
            "inputs/wolfi-python313-runtime.apko.yaml": b"c",
            "inputs/wolfi-python313-runtime.apko.lock.json": b"d",
        }
        for profile in ("transaction-api-runtime", "transaction-api-test",
                        "fds-engine-runtime", "fds-engine-test"):
            payloads["profiles/" + profile + "/roots.txt"] = b"x"
            payloads["profiles/" + profile + "/requirements.lock"] = b"y"
            payloads["profiles/" + profile + "/inventory.json"] = b"[]"
        sums = "".join(
            hashlib.sha256(raw).hexdigest() + "  " + name + "\n"
            for name, raw in sorted(payloads.items())
        ).encode()
        payloads["SHA256SUMS"] = sums
        with zipfile.ZipFile(self.artifact, "w", compression=zipfile.ZIP_STORED) as archive:
            for name, raw in payloads.items():
                archive.writestr(name, raw)
        handoff = {
            "schema": "P1-CICD-apko-supply-handoff-1",
            "artifact": {
                "id": 1,
                "name": "fixture.zip",
                "size_bytes": self.artifact.stat().st_size,
                "archive_sha256": tool.sha256(self.artifact),
                "manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
                "workflow_run_id": 1,
                "expires_on": "2099-01-01",
            },
            "source": {
                "repository": "321Team/fds-msa",
                "commit": SOURCE_SHA,
                "approved_by": "fixture source reviewer",
                "approval_reference": "https://github.com/fixture/source-review",
            },
            "supply_tool": {
                "repository": "321Team/fds-msa",
                "commit": TOOL_SHA,
                "approved_by": "fixture supply reviewer",
                "approval_reference": "https://github.com/fixture/supply-review",
            },
            "policy": {
                "purpose": "ON_PREM_TEST_BUILD_SCAN_SUPPLY_ONLY",
                "delivery_authorization": False,
                "harbor_push": "NOT_EXECUTED",
                "kubernetes_deploy": "NOT_EXECUTED",
            },
        }
        self.handoff_path.write_text(json.dumps(handoff))
        return handoff

    def run_tool(self):
        return tool.main([
            "--artifact-zip", str(self.artifact),
            "--target-parent", str(self.parent),
            "--handoff", str(self.handoff_path),
            "--test-scope", str(self.scope_path),
            "--output", str(self.output),
        ])

    def test_pending_scope_blocks_before_supply_directory_or_config(self):
        self.build_artifact_and_handoff()
        scope = approved_scope()
        scope["status"] = "PENDING_APP_LEAD_CONFIRMATION"
        self.scope_path.write_text(json.dumps(scope))
        self.assertEqual(self.run_tool(), 1)
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.parent.iterdir()), [])

    def assert_cli_rejects_link(self, expected_error):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(self.run_tool(), 1)
        self.assertIn("ERROR_CODE=" + expected_error, stdout.getvalue())
        self.assertFalse(self.output.exists())

    def test_parent_link_rejected_before_input_reads(self):
        # Cross-platform boundary test; real filesystem links are covered below.
        original = Path.is_symlink
        resolve = Path.resolve
        target = self.root / "actual-home"
        target.mkdir()
        with patch.object(Path, "is_symlink", lambda p: p == self.parent or original(p)), \
                patch.object(Path, "resolve", lambda p, **kw: target if p == self.parent else resolve(p, **kw)), \
                patch.object(tool, "read_json") as read:
            self.assert_cli_rejects_link("TARGET_PARENT_INVALID")
        read.assert_not_called()
        self.assertEqual(list(self.parent.iterdir()), [])
        self.assertEqual(list(target.iterdir()), [])

    def test_artifact_link_rejected_before_archive_reads(self):
        self.build_artifact_and_handoff()
        self.scope_path.write_text(json.dumps(approved_scope()))
        link = self.root / "artifact-link.zip"
        self.artifact = link
        original = Path.is_symlink
        with patch.object(Path, "is_symlink", lambda p: p == link or original(p)), \
                patch.object(tool, "sha256") as digest:
            self.assert_cli_rejects_link("ARTIFACT_FILE_INVALID")
        digest.assert_not_called()
        self.assertEqual(list(self.parent.iterdir()), [])

    @unittest.skipIf(os.name == "nt", "Real symlink fixtures require Linux CI")
    def test_real_parent_links_leave_target_untouched(self):
        self.build_artifact_and_handoff()
        self.scope_path.write_text(json.dumps(approved_scope()))
        target = self.parent
        for dangling in (False, True):
            with self.subTest(dangling=dangling):
                link = self.root / ("missing-home-link" if dangling else "home-link")
                link.symlink_to(self.root / "missing-home" if dangling else target,
                                target_is_directory=True)
                self.parent = link
                self.assert_cli_rejects_link("TARGET_PARENT_INVALID")
                self.assertEqual(list(target.iterdir()), [])
                self.assertFalse((self.root / "missing-home").exists())

    @unittest.skipIf(os.name == "nt", "Real symlink fixtures require Linux CI")
    def test_real_artifact_links_leave_supply_untouched(self):
        self.build_artifact_and_handoff()
        self.scope_path.write_text(json.dumps(approved_scope()))
        target = self.artifact
        before = target.read_bytes()
        for dangling in (False, True):
            with self.subTest(dangling=dangling):
                link = self.root / ("missing-artifact-link" if dangling else "artifact-link")
                link.symlink_to(self.root / "missing.zip" if dangling else target)
                self.artifact = link
                self.assert_cli_rejects_link("ARTIFACT_FILE_INVALID")
                self.assertEqual(list(self.parent.iterdir()), [])
                self.assertEqual(target.read_bytes(), before)
                self.assertFalse((self.root / "missing.zip").exists())

    def test_exact_artifact_and_approved_scope_materialize_valid_v3_config(self):
        handoff = self.build_artifact_and_handoff()
        self.scope_path.write_text(json.dumps(approved_scope()))
        self.assertEqual(self.run_tool(), 0)
        config = json.loads(self.output.read_text())
        flow.validate_config(config)
        self.assertEqual(config["schema"], "P1-CICD-app-build-scan-3")
        self.assertEqual(config["source"]["commit"], SOURCE_SHA)
        self.assertEqual(config["source"]["approval_reference"],
                         handoff["source"]["approval_reference"])
        self.assertNotIn("base_image", config["services"]["transaction-api"])
        self.assertEqual(config["services"]["transaction-api"]["unit"]["approved_by"],
                         "fixture reviewer")
        supply_dir = Path(config["offline_supply"]["directory"])
        self.assertTrue(supply_dir.is_dir())
        self.assertEqual(tool.sha256(supply_dir / "SUPPLY_MANIFEST.json"),
                         handoff["artifact"]["manifest_sha256"])

    def test_artifact_tamper_and_traversal_fail_closed(self):
        handoff = self.build_artifact_and_handoff()
        self.scope_path.write_text(json.dumps(approved_scope()))
        self.artifact.write_bytes(self.artifact.read_bytes() + b"x")
        self.assertEqual(self.run_tool(), 1)
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.parent.iterdir()), [])

        self.artifact.unlink()
        with zipfile.ZipFile(self.artifact, "w") as archive:
            archive.writestr("../escape", b"x")
            archive.writestr("SHA256SUMS", b"")
            archive.writestr("SUPPLY_MANIFEST.json", b"{}")
        handoff["artifact"]["size_bytes"] = self.artifact.stat().st_size
        handoff["artifact"]["archive_sha256"] = tool.sha256(self.artifact)
        self.handoff_path.write_text(json.dumps(handoff))
        self.assertEqual(self.run_tool(), 1)
        self.assertFalse((self.root / "escape").exists())


if __name__ == "__main__":
    unittest.main()
