"""Fixed intentional unittest failure; no caller-supplied commands or credentials."""
import json
import re
import uuid

MARKER = 'P1_CICD_INTENTIONAL_UNIT_FAILURE'
LABEL = 'unit-negative-start'
PROGRAM = (
    'import sys,unittest; '
    'case=type("DeliveryNegativeUnit",(unittest.TestCase,),'
    '{"test_expected_failure":lambda self:self.fail("' + MARKER + '")}); '
    'result=unittest.TextTestRunner(verbosity=2).run('
    'unittest.defaultTestLoader.loadTestsFromTestCase(case)); '
    'sys.exit(0 if result.wasSuccessful() else 1)'
)
INSPECT = ('{"id":{{json .ID}},"name":{{json .Name}},'
           '"owner":{{json (index .Config.Labels "io.fds.unit-negative")}},'
           '"image":{{json .Image}},"running":{{json .State.Running}}}')


class NegativeError(ValueError):
    pass


def need(value, code):
    if not value:
        raise NegativeError(code)


def read(path):
    need(path.is_file() and not path.is_symlink() and path.stat().st_size <= 1024**2,
         'NEGATIVE_OUTPUT_INVALID')
    return path.read_text().strip()


def run(command, podman, image, evidence, receipt):
    """Preserve start rc=1 via the caller's ordinary fail-closed command runner."""
    need(re.fullmatch(r'sha256:[0-9a-f]{64}', image), 'NEGATIVE_IMAGE_INVALID')
    owner = 'fds-unit-negative-' + uuid.uuid4().hex
    folder = evidence / 'unit-negative'
    folder.mkdir(mode=0o700)
    cidfile = folder / 'container.cid'
    receipt.update(scope='FIXED_UNIT_FAILURE_AFTER_APPROVED_APP_UNIT',
                   status='NOT_CONFIRMED', cleanup='NOT_CONFIRMED')
    cid = None

    def call(label, args, timeout=20):
        return command('unit-negative-' + label, podman + args, timeout=timeout)

    def identify(label):
        value = read(cidfile)
        need(re.fullmatch('[0-9a-f]{64}', value), 'NEGATIVE_CID_INVALID')
        data = json.loads(read(call(label, ['inspect', '--format', INSPECT, value])))
        need(data.get('id') == value and data.get('name', '').lstrip('/') == owner and
             data.get('owner') == owner and
             data.get('image', '').removeprefix('sha256:') == image.removeprefix('sha256:'),
             'NEGATIVE_CONTAINER_IDENTITY')
        return value

    try:
        call('create', ['create', '--name', owner, '--label', 'io.fds.unit-negative=' + owner,
             '--cidfile', str(cidfile), '--pull=never', '--network=none', '--http-proxy=false',
             '--user=10001:999', '--read-only=true', '--read-only-tmpfs=false',
             '--image-volume=ignore', '--cap-drop=ALL', '--security-opt=no-new-privileges',
             '--restart=no', '--pids-limit=32', '--memory=128m', '--env=HOME=/tmp',
             '--entrypoint=python3.13', image, '-I', '-B', '-c', PROGRAM], timeout=30)
        cid = identify('identity')
        receipt['container_id'] = cid
        call('start', ['start', '--attach', cid], timeout=30)
        # Even an unexpected zero exit must never enter Contract/Build/Scan.
        raise NegativeError('NEGATIVE_UNEXPECTED_ZERO_EXIT')
    finally:
        try:
            # Revalidate the exact owned ID before removing it; no prune/name wildcard.
            if cid is not None or cidfile.exists():
                cid = identify('cleanup-identity')
                receipt['container_id'] = cid
                call('remove', ['rm', '--force', '--time=5', cid], timeout=15)
                remaining = read(call('removed', ['ps', '--all', '--no-trunc',
                                      '--filter', 'id=' + cid, '--format', '{{.ID}}']))
                need(not remaining, 'NEGATIVE_CONTAINER_REMAINS')
                receipt['cleanup'] = 'REMOVED'
        except Exception:
            receipt['cleanup'] = 'NOT_CONFIRMED'


def observed(result, evidence):
    """Do not classify preflight errors, signals, timeout or cleanup gaps as the case."""
    step = [x for x in result.get('steps', []) if x.get('name') == LABEL]
    if not (result.get('BUILD_SCAN') == 'FAIL' and
            result.get('error_code') == 'COMMAND_FAILED:' + LABEL and
            len(step) == 1 and type(step[0].get('exit_code')) is int and
            step[0]['exit_code'] == 1 and result.get('images') == {} and
            result.get('unit_negative', {}).get('cleanup') == 'REMOVED'):
        return 'NOT_CONFIRMED'
    try:
        output = read(evidence / (LABEL + '.log'))
        from hashlib import sha256
        need(sha256((evidence / (LABEL + '.log')).read_bytes()).hexdigest() ==
             step[0]['log_sha256'], 'NEGATIVE_LOG_CHANGED')
        need(MARKER in output and 'Ran 1 test' in output and 'FAILED (failures=1)' in output,
             'NEGATIVE_UNIT_FAILURE_NOT_OBSERVED')
        need(not any('-contract-' in x['name'] or x['name'].endswith(('-build', '-save'))
                     for x in result['steps']) and not (evidence / 'scan').exists(),
             'NEGATIVE_DOWNSTREAM_OBSERVED')
    except (NegativeError, OSError, KeyError):
        return 'NOT_CONFIRMED'
    return 'EXPECTED_FAILURE_OBSERVED'
