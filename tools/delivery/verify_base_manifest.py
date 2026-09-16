#!/usr/bin/env python3
"""Offline, read-only check of supplied base manifest bytes; never an approval."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools' / 'trivy'))
from prepare_db import PreflightError, require, unique_object, utcnow  # noqa: E402

MAX_JSON = 4 * 1024 * 1024
DIGEST = r'sha256:[0-9a-f]{64}'
REPOSITORY = 'docker.io/library/python'
OCI_INDEX = 'application/vnd.oci.image.index.v1+json'
DOCKER_INDEX = 'application/vnd.docker.distribution.manifest.list.v2+json'
OCI_MANIFEST = 'application/vnd.oci.image.manifest.v1+json'
DOCKER_MANIFEST = 'application/vnd.docker.distribution.manifest.v2+json'
OCI_CONFIG = 'application/vnd.oci.image.config.v1+json'
DOCKER_CONFIG = 'application/vnd.docker.container.image.v1+json'
PROFILES = {
    OCI_MANIFEST: (OCI_CONFIG, {
        'application/vnd.oci.image.layer.v1.tar',
        'application/vnd.oci.image.layer.v1.tar+gzip',
        'application/vnd.oci.image.layer.v1.tar+zstd',
    }),
    DOCKER_MANIFEST: (DOCKER_CONFIG, {'application/vnd.docker.image.rootfs.diff.tar.gzip'}),
}


def digest(raw):
    return 'sha256:' + hashlib.sha256(raw).hexdigest()


def read_regular(path):
    """Bounded same-FD read; final symlinks/FIFOs/devices/directories refused.

    No input/output path chmod, deletion, extraction, or home directory changes.
    This is not isolation from a hostile process with the same UID.
    """
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode), 'INPUT_REGULAR_FILE_REQUIRED')
        require(0 < before.st_size <= MAX_JSON, 'INPUT_SIZE_INVALID')
        raw = stream.read(MAX_JSON + 1)
        after = os.fstat(stream.fileno())
        current = os.lstat(path)
        attributes = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        require(len(raw) == before.st_size and all(
            getattr(before, key) == getattr(after, key) == getattr(current, key)
            for key in attributes) and stat.S_ISREG(current.st_mode), 'INPUT_CHANGED')
        return raw


def json_document(raw):
    def invalid_constant(_):
        raise PreflightError('JSON_NONFINITE_NUMBER')
    value = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_object,
                       parse_constant=invalid_constant)
    require(isinstance(value, dict), 'JSON_OBJECT_REQUIRED')
    return value


def document_schema(value, types):
    require(type(value.get('schemaVersion')) is int and value['schemaVersion'] == 2,
            'SCHEMA_VERSION_UNSUPPORTED')
    require(isinstance(value.get('mediaType'), str) and value['mediaType'] in types,
            'MEDIA_TYPE_UNSUPPORTED')
    require('artifactType' not in value and 'subject' not in value, 'ARTIFACT_UNSUPPORTED')


def descriptor(value, types):
    require(isinstance(value, dict), 'DESCRIPTOR_REQUIRED')
    require(isinstance(value.get('mediaType'), str) and value['mediaType'] in types,
            'DESCRIPTOR_MEDIA_TYPE_UNSUPPORTED')
    require(isinstance(value.get('digest'), str) and re.fullmatch(DIGEST, value['digest']),
            'DESCRIPTOR_DIGEST_INVALID')
    require(type(value.get('size')) is int and value['size'] > 0, 'DESCRIPTOR_SIZE_INVALID')
    require(not any(key in value for key in ('urls', 'data', 'artifactType')),
            'EXTERNAL_INLINE_OR_ARTIFACT_UNSUPPORTED')


def match_blob(value, raw, types):
    descriptor(value, types)
    require(value['size'] == len(raw), 'BLOB_SIZE_MISMATCH')
    require(value['digest'] == digest(raw), 'BLOB_DIGEST_MISMATCH')


def platform(value):
    require(isinstance(value, dict) and value.get('os') == 'linux' and
            value.get('architecture') == 'amd64', 'PLATFORM_UNSUPPORTED')
    # Do not silently accept extra CPU/OS requirements we have not verified.
    require(value.get('variant') in (None, '') and value.get('os.version') in (None, '') and
            value.get('os.features') in (None, []) and value.get('features') in (None, []),
            'PLATFORM_REQUIREMENTS_UNSUPPORTED')


def verify(manifest_raw, config_raw, expected_digest, index_raw=None):
    """Compare supplied content to a caller-provided root digest, not provenance."""
    require(isinstance(expected_digest, str) and re.fullmatch(DIGEST, expected_digest),
            'EXPECTED_DIGEST_REQUIRED')
    for raw in (manifest_raw, config_raw, *([index_raw] if index_raw is not None else [])):
        require(isinstance(raw, bytes) and 0 < len(raw) <= MAX_JSON, 'INPUT_SIZE_INVALID')
    root_raw = index_raw if index_raw is not None else manifest_raw
    require(digest(root_raw) == expected_digest, 'ROOT_DIGEST_MISMATCH')
    if index_raw is not None:
        index = json_document(index_raw)
        document_schema(index, {OCI_INDEX, DOCKER_INDEX})
        entries = index.get('manifests')
        require(isinstance(entries, list) and 0 < len(entries) <= 256 and
                all(isinstance(entry, dict) for entry in entries), 'INDEX_ENTRIES_INVALID')
        selected = [entry for entry in entries if isinstance(entry.get('platform'), dict) and
                    entry['platform'].get('os') == 'linux' and
                    entry['platform'].get('architecture') == 'amd64']
        require(len(selected) == 1, 'UNIQUE_LINUX_AMD64_REQUIRED')
        platform(selected[0]['platform'])
        match_blob(selected[0], manifest_raw, set(PROFILES))

    manifest = json_document(manifest_raw)
    document_schema(manifest, set(PROFILES))
    if index_raw is not None:
        require(selected[0]['mediaType'] == manifest['mediaType'], 'MANIFEST_MEDIA_TYPE_MISMATCH')
    config_type, layer_types = PROFILES[manifest['mediaType']]
    match_blob(manifest.get('config'), config_raw, {config_type})
    config = json_document(config_raw)
    platform(config)
    layers = manifest.get('layers')
    require(isinstance(layers, list) and 0 < len(layers) <= 512, 'LAYERS_REQUIRED')
    for layer in layers:
        descriptor(layer, layer_types)  # Layer bytes are deliberately not supplied/verified.
    rootfs = config.get('rootfs')
    require(isinstance(rootfs, dict) and rootfs.get('type') == 'layers', 'ROOTFS_INVALID')
    diff_ids = rootfs.get('diff_ids')
    require(isinstance(diff_ids, list) and len(diff_ids) == len(layers) and
            all(isinstance(value, str) and re.fullmatch(DIGEST, value) for value in diff_ids),
            'DIFF_IDS_INVALID')
    return {
        'BASE_MANIFEST_CHECK': 'PASS', 'platform': 'linux/amd64',
        'repository_label': REPOSITORY, 'expected_root_digest': expected_digest,
        'index_digest': digest(index_raw) if index_raw is not None else None,
        'manifest_digest': digest(manifest_raw), 'config_digest': digest(config_raw),
        'base_reference_candidate': REPOSITORY + '@' + digest(manifest_raw),
        'manifest_media_type': manifest['mediaType'], 'layer_count': len(layers),
        'raw_sizes': {'index': len(index_raw) if index_raw is not None else None,
                      'manifest': len(manifest_raw), 'config': len(config_raw)},
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--index', type=Path, help='Optional raw registry index/list JSON')
    parser.add_argument('--manifest', type=Path, required=True, help='Raw image manifest JSON')
    parser.add_argument('--config', type=Path, required=True, help='Raw referenced config JSON')
    parser.add_argument('--expected-digest', required=True, help='Recorded index digest, or direct manifest digest')
    args = parser.parse_args(argv)
    result = {'schema': 'P1-CICD-base-manifest-check-1', 'BASE_MANIFEST_CHECK': 'FAIL',
              'verified_at': utcnow().isoformat(), 'NETWORK': 'NOT_EXECUTED',
              'REGISTRY_PROVENANCE': 'NOT_VERIFIED', 'SUPPLY_APPROVAL': 'NOT_VERIFIED',
              'LAYER_BLOBS': 'NOT_VERIFIED', 'IMAGE_SCAN': 'NOT_EXECUTED',
              'DELIVERY_GATE': 'NOT_VERIFIED'}
    rc = 1
    try:
        result.update(verify(read_regular(args.manifest), read_regular(args.config),
                             args.expected_digest, read_regular(args.index) if args.index else None))
        rc = 0
    except PreflightError as exc:
        result['error_code'] = str(exc)
    except (UnicodeError, ValueError, RecursionError):
        result['error_code'] = 'JSON_INVALID'
    except OSError:
        result['error_code'] = 'INPUT_READ_FAILED'
    print(json.dumps(result, indent=2, sort_keys=True))
    return rc


if __name__ == '__main__':
    raise SystemExit(main())
