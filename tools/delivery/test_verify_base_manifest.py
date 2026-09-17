"""Synthetic raw bytes and actual local files; no registry or image execution."""
import contextlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import verify_base_manifest as check


def encode(value):
    return json.dumps(value, separators=(',', ':')).encode()


def desc(raw, media_type, **extra):
    return {'mediaType': media_type, 'digest': check.digest(raw), 'size': len(raw), **extra}


def fixture(docker=False):
    config = encode({'os': 'linux', 'architecture': 'amd64',
                     'config': {'Env': ['PRIVATE_FIXTURE=do-not-echo']},
                     'rootfs': {'type': 'layers', 'diff_ids': ['sha256:' + 'a' * 64]}})
    manifest_type = check.DOCKER_MANIFEST if docker else check.OCI_MANIFEST
    config_type, layers = check.PROFILES[manifest_type]
    manifest = encode({'schemaVersion': 2, 'mediaType': manifest_type,
                       'config': desc(config, config_type),
                       'layers': [desc(b'synthetic-layer-not-supplied', sorted(layers)[0])]})
    selected = desc(manifest, manifest_type, platform={'os': 'linux', 'architecture': 'amd64'})
    index = encode({'schemaVersion': 2, 'mediaType': check.DOCKER_INDEX if docker else check.OCI_INDEX,
                    'manifests': [desc(b'arm-fixture', manifest_type,
                                       platform={'os': 'linux', 'architecture': 'arm64'}),
                                  selected, desc(b'attestation-fixture', manifest_type,
                                                 platform={'os': 'unknown', 'architecture': 'unknown'})]})
    return index, manifest, config


class ManifestTests(unittest.TestCase):
    def verify(self, index, manifest, config):
        return check.verify(manifest, config, check.digest(index or manifest), index)

    def test_oci_and_docker_index_select_child_not_index_or_config_id(self):
        for docker in (False, True):
            index, manifest, config = fixture(docker)
            with self.subTest(docker=docker):
                result = self.verify(index, manifest, config)
                self.assertEqual(result['BASE_MANIFEST_CHECK'], 'PASS')
                self.assertEqual(result['index_digest'], check.digest(index))
                self.assertEqual(result['manifest_digest'], check.digest(manifest))
                self.assertEqual(result['config_digest'], check.digest(config))
                self.assertEqual(len({result[key] for key in ('index_digest', 'manifest_digest',
                                                               'config_digest')}), 3)
                self.assertEqual(result['base_reference_candidate'],
                                 'docker.io/library/python@' + check.digest(manifest))

    def test_direct_manifest_uses_its_own_recorded_digest(self):
        for docker in (False, True):
            _, manifest, config = fixture(docker)
            result = self.verify(None, manifest, config)
            self.assertEqual(result['BASE_MANIFEST_CHECK'], 'PASS')
            self.assertIsNone(result['index_digest'])

    def test_root_digest_cannot_be_missing_config_id_or_other_content(self):
        index, manifest, config = fixture()
        for expected in (None, '', 'sha256:' + 'f' * 64, check.digest(config), check.digest(manifest)):
            with self.subTest(expected=expected), self.assertRaises(check.PreflightError):
                check.verify(manifest, config, expected, index)

    def test_raw_bytes_not_reserialized_json_are_hashed(self):
        index, manifest, config = fixture()
        reformatted = json.dumps(json.loads(manifest), indent=2).encode()
        with self.assertRaisesRegex(check.PreflightError, 'BLOB_SIZE_MISMATCH'):
            self.verify(index, reformatted, config)
        changed_index = json.loads(index)
        changed_index['manifests'][1] = desc(reformatted, check.OCI_MANIFEST,
                                            platform={'os': 'linux', 'architecture': 'amd64'})
        result = self.verify(encode(changed_index), reformatted, config)
        self.assertEqual(result['manifest_digest'], check.digest(reformatted))
        self.assertNotEqual(result['manifest_digest'], check.digest(manifest))

    def test_manifest_and_config_tamper_or_descriptor_size_mismatch_fail(self):
        index, manifest, config = fixture()
        cases = [(index, manifest.replace(b'sha256:', b'sha257:', 1), config),
                 (index, manifest, config.replace(b'amd64', b'arm64'))]
        bad_index = json.loads(index)
        bad_index['manifests'][1]['size'] += 1
        cases.append((encode(bad_index), manifest, config))
        bad_manifest = json.loads(manifest)
        bad_manifest['config']['size'] += 1
        cases.append((None, encode(bad_manifest), config))
        for inputs in cases:
            with self.subTest(inputs=inputs[:1]), self.assertRaises(check.PreflightError):
                self.verify(*inputs)

    def test_missing_or_ambiguous_amd64_never_picks_first(self):
        index, manifest, config = fixture()
        original = json.loads(index)
        for entries in ([original['manifests'][0]], [],
                        [original['manifests'][1], original['manifests'][1]]):
            candidate = {**original, 'manifests': entries}
            with self.subTest(count=len(entries)), self.assertRaises(check.PreflightError):
                self.verify(encode(candidate), manifest, config)

    def test_index_and_config_platform_requirements_checked(self):
        index, manifest, config = fixture()
        for field, value in (('architecture', 'arm64'), ('os', 'windows'), ('variant', 'v3'),
                             ('os.version', 'specific'), ('os.features', ['feature']),
                             ('features', ['special-cpu'])):
            for target in ('index', 'config'):
                idx, man, cfg = json.loads(index), json.loads(manifest), json.loads(config)
                if target == 'index':
                    idx['manifests'][1]['platform'][field] = value
                    inputs = encode(idx), manifest, config
                else:
                    cfg[field] = value
                    cfg_raw = encode(cfg)
                    man['config'] = desc(cfg_raw, check.OCI_CONFIG)
                    inputs = None, encode(man), cfg_raw
                with self.subTest(field=field, target=target), self.assertRaises(check.PreflightError):
                    self.verify(*inputs)

    def test_nested_manifest_and_media_type_or_schema_confusion_fail(self):
        index, manifest, config = fixture()
        cases = []
        for media_type in (check.OCI_INDEX, 'unknown/type', check.DOCKER_MANIFEST):
            idx = json.loads(index)
            idx['manifests'][1]['mediaType'] = media_type
            cases.append((encode(idx), manifest, config))
        for field, value in (('schemaVersion', 1), ('schemaVersion', 2.0),
                             ('mediaType', []), ('artifactType', 'example/artifact'),
                             ('subject', {})):
            man = {**json.loads(manifest), field: value}
            cases.append((None, encode(man), config))
        man = json.loads(manifest)
        man['config']['mediaType'] = check.DOCKER_CONFIG
        cases.append((None, encode(man), config))
        for inputs in cases:
            with self.subTest(inputs=inputs[:1]), self.assertRaises(check.PreflightError):
                self.verify(*inputs)

    def test_external_inline_and_artifact_descriptors_refused(self):
        index, manifest, config = fixture()
        for target in ('index', 'config', 'layer'):
            for field, value in (('urls', ['https://unapproved.invalid/secret']),
                                 ('data', 'e30='), ('artifactType', 'example/sbom'),
                                 ('size', True), ('digest', 'sha512:invalid')):
                idx, man = json.loads(index), json.loads(manifest)
                selected = idx['manifests'][1] if target == 'index' else (
                    man['config'] if target == 'config' else man['layers'][0])
                selected[field] = value
                inputs = (encode(idx), manifest, config) if target == 'index' else (None, encode(man), config)
                with self.subTest(target=target, field=field), self.assertRaises(check.PreflightError):
                    self.verify(*inputs)

    def test_layer_metadata_and_rootfs_must_agree_without_claiming_layer_verification(self):
        _, manifest, config = fixture()
        for value in (None, [], [None], [{'mediaType': 'unknown'}]):
            man = {**json.loads(manifest), 'layers': value}
            with self.subTest(layers=value), self.assertRaises(check.PreflightError):
                self.verify(None, encode(man), config)
        for value in (None, {'type': 'other'}, {'type': 'layers', 'diff_ids': []},
                      {'type': 'layers', 'diff_ids': ['not-a-digest']}):
            cfg = encode({**json.loads(config), 'rootfs': value})
            man = {**json.loads(manifest), 'config': desc(cfg, check.OCI_CONFIG)}
            with self.subTest(rootfs=value), self.assertRaises(check.PreflightError):
                self.verify(None, encode(man), cfg)


class FileAndCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.index, self.manifest, self.config = fixture()
        for name, raw in (('index', self.index), ('manifest', self.manifest), ('config', self.config)):
            (self.root / name).write_bytes(raw)
        self.argv = ['--index', str(self.root / 'index'), '--manifest', str(self.root / 'manifest'),
                     '--config', str(self.root / 'config'), '--expected-digest', check.digest(self.index)]

    def invoke(self, argv=None):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream), \
                patch('socket.socket', side_effect=AssertionError('network forbidden')), \
                patch('subprocess.Popen', side_effect=AssertionError('commands forbidden')):
            rc = check.main(argv or self.argv)
        output = stream.getvalue()
        self.assertNotIn('do-not-echo', output)
        self.assertNotIn(str(self.root), output)
        return rc, json.loads(output)

    def test_cli_success_is_only_local_integrity_and_preserves_input_bytes_modes(self):
        before = {p.name: (p.read_bytes(), stat.S_IMODE(p.stat().st_mode)) for p in self.root.iterdir()}
        parent_mode = stat.S_IMODE(self.root.stat().st_mode)
        rc, result = self.invoke()
        self.assertEqual((rc, result['BASE_MANIFEST_CHECK']), (0, 'PASS'))
        for key in ('REGISTRY_PROVENANCE', 'SUPPLY_APPROVAL', 'LAYER_BLOBS', 'DELIVERY_GATE'):
            self.assertEqual(result[key], 'NOT_VERIFIED')
        for key in ('NETWORK', 'IMAGE_SCAN'):
            self.assertEqual(result[key], 'NOT_EXECUTED')
        self.assertEqual(before, {p.name: (p.read_bytes(), stat.S_IMODE(p.stat().st_mode))
                                  for p in self.root.iterdir()})
        self.assertEqual(parent_mode, stat.S_IMODE(self.root.stat().st_mode))

    def test_bad_json_duplicate_keys_nonfinite_utf8_and_deep_nesting_fail_closed(self):
        _, manifest, _ = fixture()
        for raw in (b'{"os":"linux","os":"windows"}', b'{"private":NaN}', b'{"private":Infinity}',
                    b'\xffdo-not-echo', b'[]', b'{' + b'"a":{' * 1100 + b'}' * 1101):
            (self.root / 'config').write_bytes(raw)
            man = {**json.loads(manifest), 'config': desc(raw, check.OCI_CONFIG)}
            new_manifest = encode(man)
            (self.root / 'manifest').write_bytes(new_manifest)
            argv = self.argv[2:]
            argv[-1] = check.digest(new_manifest)
            with self.subTest(raw_prefix=raw[:20]):
                rc, result = self.invoke(argv)
                self.assertEqual((rc, result['BASE_MANIFEST_CHECK']), (1, 'FAIL'))
                self.assertNotIn('base_reference_candidate', result)

    def test_empty_oversize_directory_symlink_and_fifo_inputs_are_refused(self):
        empty = self.root / 'empty'
        empty.touch()
        big = self.root / 'big'
        with big.open('wb') as stream:
            stream.truncate(check.MAX_JSON + 1)
        link = self.root / 'link'
        link.symlink_to(self.root / 'manifest')
        fifo = self.root / 'fifo'
        os.mkfifo(fifo)
        for path in (empty, big, self.root, link, fifo):
            with self.subTest(path=path.name), self.assertRaises((check.PreflightError, OSError)):
                check.read_regular(path)
        # An actual CLI process gives FIFO rejection a finite wall-clock bound.
        argv = self.argv.copy()
        argv[3] = str(fifo)
        run = subprocess.run([sys.executable, check.__file__, *argv], capture_output=True,
                             text=True, timeout=5)
        self.assertEqual(run.returncode, 1)
        self.assertEqual(json.loads(run.stdout)['BASE_MANIFEST_CHECK'], 'FAIL')

    def test_path_replacement_during_read_is_detected(self):
        path = self.root / 'manifest'
        replacement = self.root / 'replacement'
        replacement.write_bytes(self.manifest)
        original_fstat = check.os.fstat
        calls = 0

        def replaced(fd):
            nonlocal calls
            calls += 1
            if calls == 2:
                os.replace(replacement, path)
            return original_fstat(fd)

        with patch.object(check.os, 'fstat', side_effect=replaced), \
                self.assertRaisesRegex(check.PreflightError, 'INPUT_CHANGED'):
            check.read_regular(path)

    def test_cli_failures_emit_no_candidate_or_raw_exception(self):
        for mode in ('wrong-digest', 'missing-file', 'invalid-digest'):
            argv = self.argv.copy()
            if mode == 'missing-file':
                argv[3] = str(self.root / 'missing-do-not-echo')
            else:
                argv[-1] = 'sha256:' + 'f' * 64 if mode == 'wrong-digest' else 'do-not-echo'
            with self.subTest(mode=mode):
                rc, result = self.invoke(argv)
                self.assertEqual((rc, result['BASE_MANIFEST_CHECK']), (1, 'FAIL'))
                self.assertNotIn('base_reference_candidate', result)
                self.assertEqual(result['SUPPLY_APPROVAL'], 'NOT_VERIFIED')


if __name__ == '__main__':
    unittest.main()
