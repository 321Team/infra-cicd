"""Real stdlib failing unittest; Podman lifecycle is MOCK, not on-prem evidence."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import unit_negative as case


class CommandFailed(ValueError):
    pass


class FakePodman:
    def __init__(self, folder):
        self.folder, self.steps, self.argv = folder, [], {}
        self.cid, self.image = 'a' * 64, 'sha256:' + 'b' * 64
        self.owner, self.fault = None, None

    def command(self, label, argv, timeout=20):
        output = self.folder / (label + '.log')
        rc, raw = 0, ''
        self.argv[label] = argv
        if label == 'unit-negative-create':
            self.owner = argv[argv.index('--name') + 1]
            Path(argv[argv.index('--cidfile') + 1]).write_text(self.cid)
        elif label.endswith('identity'):
            raw = json.dumps({'id': self.cid, 'image': self.image, 'name': self.owner,
                              'owner': 'other' if self.fault == 'identity' else self.owner})
        elif label == case.LABEL:
            if self.fault == 'timeout':
                output.write_text('')
                raise subprocess.TimeoutExpired(argv, timeout)
            if self.fault == 'interrupt':
                output.write_text('')
                raise KeyboardInterrupt()
            p = subprocess.run([sys.executable, '-I', '-B', '-c', case.PROGRAM],
                               capture_output=True, text=True, timeout=10)
            rc, raw = p.returncode, p.stdout + p.stderr
            if self.fault == 'zero':
                rc = 0
        elif label == 'unit-negative-remove' and self.fault == 'cleanup':
            rc = 125
        elif label == 'unit-negative-removed' and self.fault == 'remaining':
            raw = self.cid
        output.write_text(raw, encoding='utf-8')
        self.steps.append({'name': label, 'exit_code': rc,
                           'log_sha256': hashlib.sha256(output.read_bytes()).hexdigest()})
        if rc:
            raise CommandFailed('COMMAND_FAILED:' + label)
        return output


class UnitNegativeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.podman = FakePodman(self.root)
        self.receipt = {}

    def run_case(self):
        case.run(self.podman.command, ['/usr/bin/podman', '--root', str(self.root / 'storage')],
                 self.podman.image, self.root, self.receipt)

    def record(self):
        return {'BUILD_SCAN': 'FAIL', 'error_code': 'COMMAND_FAILED:' + case.LABEL,
                'steps': self.podman.steps, 'images': {}, 'unit_negative': self.receipt}

    def test_real_fixed_assertion_failure_is_preserved_and_owned_container_removed(self):
        with self.assertRaisesRegex(CommandFailed, case.LABEL):
            self.run_case()
        self.assertEqual(case.observed(self.record(), self.root), 'EXPECTED_FAILURE_OBSERVED')
        create = self.podman.argv['unit-negative-create']
        for flag in ('--pull=never', '--network=none', '--read-only=true', '--cap-drop=ALL',
                     '--security-opt=no-new-privileges', '--user=10001:999'):
            self.assertIn(flag, create)
        self.assertNotIn('--volume', create)
        self.assertEqual(create[-1], case.PROGRAM)
        self.assertEqual(self.podman.argv['unit-negative-remove'][-1], self.podman.cid)

    def test_unexpected_zero_still_stops_and_is_not_expected_failure(self):
        self.podman.fault = 'zero'
        with self.assertRaisesRegex(case.NegativeError, 'NEGATIVE_UNEXPECTED_ZERO_EXIT'):
            self.run_case()
        self.assertEqual(self.receipt['cleanup'], 'REMOVED')
        self.assertEqual(case.observed(self.record(), self.root), 'NOT_CONFIRMED')

    def test_timeout_and_interrupt_preserve_cleanup_but_never_pass(self):
        for mode, error in [('timeout', subprocess.TimeoutExpired), ('interrupt', KeyboardInterrupt)]:
            with self.subTest(mode=mode):
                folder = self.root / mode
                folder.mkdir()
                p = FakePodman(folder)
                p.fault = mode
                receipt = {}
                with self.assertRaises(error):
                    case.run(p.command, ['/usr/bin/podman'], p.image, folder, receipt)
                self.assertEqual(receipt['cleanup'], 'REMOVED')
                self.assertEqual(case.observed({'steps': p.steps}, folder), 'NOT_CONFIRMED')

    def test_cleanup_failure_is_not_accepted(self):
        self.podman.fault = 'cleanup'
        with self.assertRaises(CommandFailed):
            self.run_case()
        self.assertEqual(self.receipt['cleanup'], 'NOT_CONFIRMED')
        self.assertEqual(case.observed(self.record(), self.root), 'NOT_CONFIRMED')

    def test_identity_mismatch_must_not_delete_unowned_container(self):
        self.podman.fault = 'identity'
        with self.assertRaisesRegex(case.NegativeError, 'NEGATIVE_CONTAINER_IDENTITY'):
            self.run_case()
        self.assertNotIn('unit-negative-remove', self.podman.argv)
        self.assertEqual(self.receipt['cleanup'], 'NOT_CONFIRMED')

    def test_remaining_container_blocks_expected_case(self):
        self.podman.fault = 'remaining'
        with self.assertRaises(CommandFailed):
            self.run_case()
        self.assertEqual(case.observed(self.record(), self.root), 'NOT_CONFIRMED')

    def test_log_change_or_downstream_work_cannot_pass(self):
        with self.assertRaises(CommandFailed):
            self.run_case()
        data = self.record()
        for label in ('fds-engine-contract-0', 'transaction-api-build', 'fds-engine-save'):
            mutated = copy.deepcopy(data)
            mutated['steps'].append({'name': label, 'exit_code': 0})
            self.assertEqual(case.observed(mutated, self.root), 'NOT_CONFIRMED')
        (self.root / (case.LABEL + '.log')).write_text('different output')
        self.assertEqual(case.observed(data, self.root), 'NOT_CONFIRMED')

    def test_missing_bad_return_code_and_old_evidence_rejected(self):
        with self.assertRaises(CommandFailed):
            self.run_case()
        for rc in (0, 2, 125, 137, True, '1'):
            mutated = copy.deepcopy(self.record())
            next(x for x in mutated['steps'] if x['name'] == case.LABEL)['exit_code'] = rc
            self.assertEqual(case.observed(mutated, self.root), 'NOT_CONFIRMED')
        with self.assertRaises(FileExistsError):
            self.run_case()


if __name__ == '__main__':
    unittest.main()
