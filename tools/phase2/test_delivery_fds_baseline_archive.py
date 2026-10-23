"""SYNTHETIC archives/grants only; actual private objects never enter tests."""
from dataclasses import replace
import gzip
import hashlib
import io
import json
from pathlib import Path
import socket
import tarfile
import unittest
from unittest.mock import patch

import delivery_adapter as core
import delivery_fds_baseline_archive as reader
import test_delivery_fds_existing_inputs as fixture

ROOT = 'SYNTHETIC-baseline6'


def archive(data, *, extra=(), manifest=None, omit=(), duplicate=False):
    rows = [(name, value) for name, value in data.items() if name not in omit]
    sums = manifest if manifest is not None else ''.join(
        core.digest(value) + '  ' + name + '\n' for name, value in rows).encode()
    rows.append(('SHA256SUMS', sums))
    with io.BytesIO() as output:
        with tarfile.open(fileobj=output, mode='w', format=tarfile.USTAR_FORMAT) as tar:
            directory = tarfile.TarInfo(ROOT); directory.type = tarfile.DIRTYPE
            tar.addfile(directory)
            for name, value in reversed(rows):  # Native tar order is not role order.
                info = tarfile.TarInfo(ROOT + '/' + name); info.size = len(value)
                tar.addfile(info, io.BytesIO(value))
            if duplicate:
                name, value = rows[0]; info = tarfile.TarInfo(ROOT+'/'+name); info.size = len(value)
                tar.addfile(info, io.BytesIO(value))
            for info, value in extra:
                tar.addfile(info, io.BytesIO(value) if value else None)
        return gzip.compress(output.getvalue(), mtime=0)


class BaselineArchiveTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.ExistingProfileTests('runTest'); self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.data = dict(zip(reader.OBJECT_FILES, (raw+b'\n' for raw in self.f.baseline.objects)))
        self.data.update({name: b'SYNTHETIC_NOTE_NOT_AUTHORITY\n' for name in reader.NOTES})
        self.originals = tuple(self.data[name] for name in reader.OBJECT_FILES)
        self.plan = replace(self.f.plan, baseline=None)
        for method in ('socket', 'create_connection', 'getaddrinfo'):
            guard = patch.object(socket, method, side_effect=AssertionError('NO_NETWORK'))
            guard.start(); self.addCleanup(guard.stop)

    def read(self, raw=None, **changes):
        raw = archive(self.data) if raw is None else raw
        return reader.decode_baseline_archive(raw, **(dict(
            expected_sha256=core.digest(raw), root_name=ROOT) | changes))

    def connect(self, **changes):
        raw = archive(self.data)
        args = dict(archive=raw, expected_sha256=core.digest(raw), root_name=ROOT,
                    grant=self.f.baseline.grant)
        args.update(changes)
        return reader.connect_baseline_archive(self.f.config, self.plan, **args)

    def test_exact_order_and_original_whitespace_are_preserved_without_file_io(self):
        with patch.object(Path, 'open', side_effect=AssertionError('NO_FILE')):
            values = self.read()
        self.assertIs(type(values), tuple)
        self.assertEqual(self.originals, values)
        self.assertTrue(all(v.endswith(b'\n') for v in values))
        self.assertEqual(0, json.loads(values[2])['spec']['replicas'])

    def test_connect_retains_supplied_grant_previous_and_all_other_plan_roles(self):
        connected = self.connect()
        self.assertIsNone(self.plan.baseline)
        self.assertIs(connected.baseline.grant, self.f.baseline.grant)
        self.assertEqual(self.originals, connected.baseline.objects)
        for name in ('approval', 'issuer', 'producer', 'subject'):
            self.assertIs(getattr(self.plan, name), getattr(connected, name))
        self.assertIs(connected.producer.facts.previous_images, self.f.previous)

    def test_missing_previous_or_mismatched_previous_is_never_inferred_from_raw(self):
        for previous in (None, self.f.previous[:1], tuple(reversed(self.f.previous))):
            self.plan = replace(self.plan, producer=replace(self.plan.producer,
                facts=replace(self.plan.producer.facts, previous_images=previous)))
            with self.assertRaisesRegex(reader.BaselineArchiveError, 'CONNECTION_INVALID'):
                self.connect()
        self.assertIsNone(self.plan.baseline)

    def test_existing_baseline_foreign_binding_or_plain_grant_never_overwrites(self):
        for grant in ({'approved': True}, replace(self.f.baseline.grant,
                      binding=replace(self.f.config.binding, run_id='SYNTHETIC_OTHER'))):
            with self.assertRaises(reader.BaselineArchiveError): self.connect(grant=grant)
        self.plan = self.f.plan
        with self.assertRaises(reader.BaselineArchiveError): self.connect()

    def test_external_digest_is_required_before_decompression(self):
        raw = archive(self.data)
        with patch.object(gzip, 'GzipFile', side_effect=AssertionError('MUST_NOT_DECOMPRESS')) as opened:
            for digest in ('0'*64, '', True, core.digest(raw).upper()):
                with self.assertRaises(reader.BaselineArchiveError):
                    self.read(raw, expected_sha256=digest)
        opened.assert_not_called()

    def test_unlisted_missing_duplicate_link_or_escape_members_are_rejected(self):
        candidates = [archive(self.data, omit=(reader.OBJECT_FILES[0],)),
                      archive(self.data, duplicate=True)]
        for name, kind in ((ROOT+'/extra.json', tarfile.REGTYPE),
                           (ROOT+'/../escape', tarfile.REGTYPE),
                           ('/absolute', tarfile.REGTYPE),
                           (ROOT+'/link', tarfile.SYMTYPE),
                           (ROOT+'/hard', tarfile.LNKTYPE)):
            info = tarfile.TarInfo(name); info.type = kind
            info.linkname = '/SYNTHETIC'; info.size = 1 if kind == tarfile.REGTYPE else 0
            candidates.append(archive(self.data, extra=((info, b'x' if info.size else b''),)))
        for raw in candidates:
            with self.subTest(sha=hashlib.sha256(raw).hexdigest()), self.assertRaises(reader.BaselineArchiveError):
                self.read(raw)

    def test_manifest_missing_duplicate_self_or_wrong_digest_is_rejected(self):
        good = ''.join(core.digest(v)+'  '+k+'\n' for k,v in self.data.items()).encode()
        for bad in (b'', good+good.splitlines(keepends=True)[0],
                    good+b'0'*64+b'  SHA256SUMS\n', good.replace(good[:64], b'0'*64, 1)):
            with self.assertRaises(reader.BaselineArchiveError): self.read(archive(self.data, manifest=bad))

    def test_json_identity_namespace_uid_and_owner_shape_use_existing_checker(self):
        name = reader.OBJECT_FILES[0]
        changes = (lambda o:o['metadata'].update(namespace='SYNTHETIC_OTHER'),
                   lambda o:o['metadata'].pop('managedFields'),
                   lambda o:o['metadata'].update(deletionTimestamp='SYNTHETIC'),
                   lambda o:o['metadata']['managedFields'][0].update(operation='UNKNOWN'),
                   lambda o:o['metadata'].update(uid=json.loads(self.originals[1])['metadata']['uid']))
        for change in changes:
            data = dict(self.data); obj = json.loads(data[name]); change(obj); data[name] = core.encoded(obj)
            with self.assertRaises(reader.BaselineArchiveError): self.read(archive(data))
        data = dict(self.data); data[name] = b'{"kind":"Service","kind":"Service"}'
        with self.assertRaises(reader.BaselineArchiveError): self.read(archive(data))

    def test_compressed_and_uncompressed_limits_and_bad_root_are_bounded(self):
        for raw in (b'x'*(reader.MAX_ARCHIVE+1), gzip.compress(b'x'*(reader.MAX_TAR+1)), b'NOT_GZIP'):
            with self.assertRaises(reader.BaselineArchiveError): self.read(raw)
        for root in ('../escape', '/absolute', ROOT+'/other', True):
            with self.assertRaises(reader.BaselineArchiveError): self.read(root_name=root)


if __name__ == '__main__': unittest.main()
