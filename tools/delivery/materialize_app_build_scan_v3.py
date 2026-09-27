#!/usr/bin/env python3
"""Prepare the approved apko artifact and materialize a schema-v3 app config.

This is a pre-runtime handoff tool. It never runs Podman, Trivy, Harbor,
Kubernetes, or application code. The produced config remains subject to
independent review before the on-prem Test->Build->Scan workflow is run.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import zipfile

import build_scan as flow
import offline_supply_v3 as supply

HEX = re.compile(r"[0-9a-f]{64}")
SHA_LINE = re.compile(r"([0-9a-f]{64})  ([A-Za-z0-9_.+/-]+)")
SOURCE_INPUT_MAP = {
    "ci/apko/wolfi-python313-builder.apko.yaml": "wolfi-python313-builder.apko.yaml",
    "ci/apko/wolfi-python313-builder.apko.lock.json": "wolfi-python313-builder.apko.lock.json",
    "ci/apko/wolfi-python313-runtime.apko.yaml": "wolfi-python313-runtime.apko.yaml",
    "ci/apko/wolfi-python313-runtime.apko.lock.json": "wolfi-python313-runtime.apko.lock.json",
}
REQUIREMENT_KEYS = {
    service + "/" + filename
    for service in flow.SERVICES
    for filename in ("requirements.txt", "requirements-test.txt")
}


def sha256(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise flow.PreflightError("DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def read_json(path, limit=1024 * 1024):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise flow.PreflightError("INPUT_FILE_INVALID")
    return json.loads(path.read_bytes(), object_pairs_hook=unique_object)


def relative(name):
    if (not isinstance(name, str) or not name or name.startswith("/")
            or "\\" in name or any(part in ("", ".", "..") for part in name.split("/"))):
        raise flow.PreflightError("ARCHIVE_PATH_INVALID")
    return name


def validate_handoff(data):
    required = {"schema", "artifact", "source", "supply_tool", "policy"}
    flow.require(isinstance(data, dict) and set(data) == required
                 and data.get("schema") == "P1-CICD-apko-supply-handoff-1",
                 "HANDOFF_SCHEMA_INVALID")
    artifact = data["artifact"]
    flow.require(isinstance(artifact, dict) and set(artifact) == {
        "id", "name", "size_bytes", "archive_sha256", "manifest_sha256",
        "workflow_run_id", "expires_on"}, "HANDOFF_ARTIFACT_FIELDS")
    flow.require(type(artifact["id"]) is int and artifact["id"] > 0
                 and type(artifact["size_bytes"]) is int and artifact["size_bytes"] > 0
                 and type(artifact["workflow_run_id"]) is int and artifact["workflow_run_id"] > 0
                 and isinstance(artifact["archive_sha256"], str)
                 and HEX.fullmatch(artifact["archive_sha256"])
                 and isinstance(artifact["manifest_sha256"], str)
                 and HEX.fullmatch(artifact["manifest_sha256"]),
                 "HANDOFF_ARTIFACT_INVALID")
    for key in ("source", "supply_tool"):
        item = data[key]
        flow.require(isinstance(item, dict) and set(item) == {
            "repository", "commit", "approved_by", "approval_reference"},
            "HANDOFF_APPROVAL_FIELDS")
        flow.require(item["repository"] == "321Team/fds-msa"
                     and isinstance(item["commit"], str)
                     and re.fullmatch(r"[0-9a-f]{40}", item["commit"])
                     and isinstance(item["approved_by"], str) and item["approved_by"].strip()
                     and isinstance(item["approval_reference"], str)
                     and item["approval_reference"].startswith("https://github.com/"),
                     "HANDOFF_APPROVAL_INVALID")
    policy = data["policy"]
    flow.require(policy == {
        "purpose": "ON_PREM_TEST_BUILD_SCAN_SUPPLY_ONLY",
        "delivery_authorization": False,
        "harbor_push": "NOT_EXECUTED",
        "kubernetes_deploy": "NOT_EXECUTED",
    }, "HANDOFF_POLICY_INVALID")
    return data


def validate_scope(data, source_sha):
    flow.require(isinstance(data, dict) and set(data) == {
        "schema", "status", "source_commit", "approval", "services"},
        "TEST_SCOPE_FIELDS")
    flow.require(data.get("schema") == "P1-CICD-test-scope-proposal-1"
                 and data.get("status") == "APPROVED"
                 and data.get("source_commit") == source_sha,
                 "TEST_SCOPE_NOT_APPROVED")
    approval = data["approval"]
    flow.require(isinstance(approval, dict) and set(approval) == {
        "approved_by", "approval_reference"}
        and isinstance(approval["approved_by"], str) and approval["approved_by"].strip()
        and isinstance(approval["approval_reference"], str)
        and approval["approval_reference"].startswith("https://github.com/"),
        "TEST_SCOPE_APPROVAL_REQUIRED")
    services = data["services"]
    flow.require(isinstance(services, dict) and set(services) == set(flow.SERVICES),
                 "TEST_SCOPE_SERVICES_INVALID")
    result = {}
    for name in flow.SERVICES:
        item = services[name]
        flow.require(isinstance(item, dict) and set(item) == {"unit", "contract"},
                     "TEST_SCOPE_SERVICE_FIELDS")
        unit = item["unit"]
        if isinstance(unit, dict) and unit.get("status") == "NOT_APPLICABLE":
            flow.require(set(unit) == {"status", "reason"}
                         and isinstance(unit["reason"], str) and unit["reason"].strip(),
                         "TEST_SCOPE_NA_INVALID")
            unit = {**unit, **approval}
        else:
            unit = unit
        flow.validate_unit(unit)
        flow.test_step(item["contract"])
        result[name] = {"unit": unit, "contract": item["contract"]}
    return result


def parse_sums(raw):
    result = {}
    for line in raw.decode("utf-8").splitlines():
        match = SHA_LINE.fullmatch(line)
        flow.require(match is not None, "SHA256SUMS_LINE_INVALID")
        digest, name = match.groups()
        name = relative(name)
        flow.require(name != "SHA256SUMS" and name not in result, "SHA256SUMS_DUPLICATE")
        result[name] = digest
    flow.require(result, "SHA256SUMS_EMPTY")
    return result


def extract_verified(artifact, parent, handoff):
    if artifact.is_symlink() or not artifact.is_file():
        raise flow.PreflightError("ARTIFACT_FILE_INVALID")
    expected = handoff["artifact"]
    flow.require(artifact.stat().st_size == expected["size_bytes"],
                 "ARTIFACT_SIZE_MISMATCH")
    flow.require(sha256(artifact) == expected["archive_sha256"],
                 "ARTIFACT_SHA256_MISMATCH")
    source_sha = handoff["source"]["commit"]
    manifest_sha = expected["manifest_sha256"]
    final = parent / ("FDS_CICD_ApkoSupply." + source_sha[:12] + "." + manifest_sha[:12])
    flow.require(not final.exists(), "SUPPLY_TARGET_EXISTS")
    temp = Path(tempfile.mkdtemp(prefix=".FDS_CICD_ApkoSupply.", dir=parent))
    try:
        with zipfile.ZipFile(artifact) as archive:
            seen = set()
            files = {}
            total = 0
            for info in archive.infolist():
                name = info.filename.rstrip("/") if info.is_dir() else info.filename
                name = relative(name)
                flow.require(name not in seen and len(seen) < 1024, "ARTIFACT_MEMBER_DUPLICATE_OR_TOO_MANY")
                seen.add(name)
                mode = (info.external_attr >> 16) & 0xFFFF
                flow.require(not stat.S_ISLNK(mode), "ARTIFACT_SYMLINK_NOT_ALLOWED")
                if info.is_dir():
                    continue
                total += info.file_size
                flow.require(total <= 512 * 1024 * 1024, "ARTIFACT_EXPANDED_SIZE_LIMIT")
                files[name] = info
            flow.require("SHA256SUMS" in files and "SUPPLY_MANIFEST.json" in files,
                         "ARTIFACT_REQUIRED_FILES_MISSING")
            sums = parse_sums(archive.read(files["SHA256SUMS"]))
            flow.require(set(sums) == set(files) - {"SHA256SUMS"},
                         "ARTIFACT_FILESET_MISMATCH")
            for name, info in files.items():
                target = temp / name
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                raw = archive.read(info)
                target.write_bytes(raw)
                os.chmod(target, 0o600)
                if name != "SHA256SUMS":
                    flow.require(hashlib.sha256(raw).hexdigest() == sums[name],
                                 "ARTIFACT_MEMBER_HASH_MISMATCH:" + name)
        manifest = temp / "SUPPLY_MANIFEST.json"
        flow.require(sha256(manifest) == manifest_sha, "SUPPLY_MANIFEST_HASH_MISMATCH")
        data = read_json(manifest)
        flow.require(data.get("schema") == "fds.apko-offline-supply.v1"
                     and data.get("source") == {
                         "repository": handoff["source"]["repository"],
                         "commit": handoff["source"]["commit"]}
                     and data.get("supply_tool") == {
                         "repository": handoff["supply_tool"]["repository"],
                         "commit": handoff["supply_tool"]["commit"]}
                     and data.get("policy") == {
                         "delivery_authorization": False,
                         "harbor_push": "NOT_EXECUTED",
                         "high_critical_exception": False,
                         "kubernetes_deploy": "NOT_EXECUTED",
                         "purpose": "ON_PREM_TEST_BUILD_SCAN_SUPPLY_ONLY"},
                     "SUPPLY_MANIFEST_BINDING_MISMATCH")
        os.rename(temp, final)
        return final, data
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        raise


def make_config(directory, manifest, handoff, scope):
    flow.require(set(manifest.get("apko_inputs", {})) == set(SOURCE_INPUT_MAP.values()),
                 "SUPPLY_APKO_INPUTS_INVALID")
    source_inputs = {
        source: manifest["apko_inputs"][name]
        for source, name in SOURCE_INPUT_MAP.items()
    }
    requirements = manifest.get("requirements", {})
    flow.require(REQUIREMENT_KEYS <= set(requirements), "SUPPLY_REQUIREMENTS_INCOMPLETE")
    offline = {
        "directory": str(directory),
        "images": manifest["images"],
        "profiles": manifest["profiles"],
        "requirements": {key: requirements[key] for key in sorted(REQUIREMENT_KEYS)},
        "source_inputs": source_inputs,
    }
    services = {}
    for name in flow.SERVICES:
        services[name] = {
            "context": name,
            "dockerfile": name + "/Dockerfile",
            "setup": [],
            "unit": scope[name]["unit"],
            "contract": scope[name]["contract"],
        }
    config = {
        "schema": "P1-CICD-app-build-scan-3",
        "status": "APPROVED_FOR_TEST_BUILD_SCAN",
        "source": handoff["source"],
        "services": services,
        "offline_supply": offline,
    }
    # build_scan only accepts the source fields used in execution.
    config["source"] = {
        key: config["source"][key]
        for key in ("repository", "commit", "approved_by", "approval_reference")
    }
    flow.validate_config(config)
    return config


def write_private(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-zip", required=True, type=Path)
    parser.add_argument("--target-parent", required=True, type=Path)
    parser.add_argument("--handoff", required=True, type=Path)
    parser.add_argument("--test-scope", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        parent = args.target_parent.resolve(strict=True)
        flow.require(parent.is_dir() and not parent.is_symlink(), "TARGET_PARENT_INVALID")
        handoff = validate_handoff(read_json(args.handoff))
        scope = validate_scope(read_json(args.test_scope), handoff["source"]["commit"])
        supply_dir, manifest = extract_verified(args.artifact_zip.resolve(strict=True), parent, handoff)
        config = make_config(supply_dir, manifest, handoff, scope)
        raw = (json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
        write_private(args.output, raw)
        print("PREPARE_V3_CONFIG=PASS")
        print("SUPPLY_DIR=" + str(supply_dir))
        print("CONFIG_PATH=" + str(args.output))
        print("CONFIG_SHA256=" + hashlib.sha256(raw).hexdigest())
        print("SOURCE_SHA=" + handoff["source"]["commit"])
        print("DELIVERY_GATE=NOT_VERIFIED")
        return 0
    except (flow.PreflightError, OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as exc:
        print("PREPARE_V3_CONFIG=FAIL")
        print("ERROR_CODE=" + (str(exc) if isinstance(exc, flow.PreflightError)
                              else "INPUT_OR_FILESYSTEM_ERROR"))
        print("DELIVERY_GATE=NOT_VERIFIED")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
