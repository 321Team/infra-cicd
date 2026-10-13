"""Gate schema3 builds on isolated final-image startup, before vulnerability Scan.

The caller supplies its private Podman storage and command/log collector. This
does not authorize Push/Deploy or verify DB readiness, business E2E or Kubernetes.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import uuid

from image_smoke_probe import PROFILES

PROBE = Path(__file__).with_name('image_smoke_probe.py')
INSPECT = ('{"id":{{json .Id}},"image":{{json .Image}},'
           '"name":{{json .Name}},"owner":{{json (index .Config.Labels "io.fds.image-smoke")}},'
           '"user":{{json .Config.User}},"workdir":{{json .Config.WorkingDir}},'
           '"path":{{json .Path}},"args":{{json .Args}},'
           '"running":{{json .State.Running}},'
           '"readonly":{{json .HostConfig.ReadonlyRootfs}},'
           '"network":{{json .HostConfig.NetworkMode}},"mounts":{{json .Mounts}}}')


class SmokeError(ValueError):
    pass


def need(condition, code):
    if not condition:
        raise SmokeError(code)


def text(path):
    need(path.stat().st_size <= 1024 * 1024, 'SMOKE_OUTPUT_TOO_LARGE')
    return path.read_text().strip()


def check_state(raw, image, cid, owner, *, running):
    data = json.loads(raw)
    need(isinstance(data, dict), 'SMOKE_INSPECT_SHAPE')
    need(data.get('id') == cid and data.get('name', '').lstrip('/') == owner and
         data.get('owner') == owner, 'SMOKE_CONTAINER_IDENTITY')
    actual = data.get('image', '')
    need(actual.removeprefix('sha256:') == image.removeprefix('sha256:'), 'SMOKE_IMAGE_IDENTITY')
    need(data.get('user') == '10001:999' and data.get('workdir') == '/app' and
         data.get('path') == '/venv/bin/python3' and data.get('args') == ['run.py'],
         'SMOKE_RUNTIME_PROFILE')
    need(data.get('running') is running and data.get('readonly') is True and
         data.get('network') == 'none' and data.get('mounts') in ([], None),
         'SMOKE_ISOLATION_OR_STATE')


def check_probe(raw, service):
    data = json.loads(raw)
    need(isinstance(data, dict) and data.get('schema') == 'P1-final-image-probe-1' and
         data.get('service') == service and data.get('uid') == 10001 and
         data.get('gid') == 999 and data.get('rootfs') == 'READ_ONLY' and
         type(data.get('readable_files')) is int and data['readable_files'] >= 1 and
         data.get('checks') == {'livez': 200, 'business_metrics': 404,
                                'dedicated_metrics': 200, 'metrics_livez': 404},
         'SMOKE_PROBE_NOT_PASS')
    return data


def check_image(command, podman, service, identity, evidence, receipt):
    """Update the build receipt even on failures; always attempt own-CID cleanup."""
    need(service in PROFILES, 'SMOKE_SERVICE_UNSUPPORTED')
    image = identity['config_digest']
    need(re.fullmatch(r'sha256:[0-9a-f]{64}', image), 'SMOKE_IMAGE_INVALID')
    owner = 'fds-image-smoke-' + uuid.uuid4().hex
    folder = evidence / (service + '-image-smoke')
    folder.mkdir(mode=0o700)  # Refuse reusing any earlier probe directory.
    cidfile = folder / 'container.cid'
    probe = PROBE.read_bytes()
    receipt.update(status='FAIL', scope='ISOLATED_IMAGE_STARTUP_ONLY',
                   identity=dict(identity), probe_sha256=hashlib.sha256(probe).hexdigest(),
                   started_at=datetime.now(timezone.utc).isoformat(), cleanup='NOT_CONFIRMED',
                   DB_READINESS='NOT_VERIFIED', APP_E2E='NOT_VERIFIED',
                   KUBERNETES_RUNTIME='NOT_VERIFIED')
    cid, primary_failed, cleanup_errors = None, False, []

    def call(suffix, argv, timeout=20):
        return command(service + '-image-smoke-' + suffix, podman + argv, timeout=timeout)

    def own_cid():
        need(not cidfile.is_symlink() and cidfile.is_file(), 'SMOKE_CID_NOT_CONFIRMED')
        value = text(cidfile)
        need(re.fullmatch('[0-9a-f]{64}', value), 'SMOKE_CID_INVALID')
        return value

    try:
        main, metrics, _ = PROFILES[service]
        call('create', ['create', '--name', owner, '--label', 'io.fds.image-smoke=' + owner,
                       '--cidfile', str(cidfile), '--pull=never', '--network=none',
                       '--http-proxy=false', '--user=10001:999', '--read-only=true',
                       '--read-only-tmpfs=false', '--image-volume=ignore', '--cap-drop=ALL',
                       '--security-opt=no-new-privileges', '--restart=no', '--pids-limit=128',
                       '--env=PYTHONDONTWRITEBYTECODE=1', '--env=PYTHONUNBUFFERED=1',
                       '--env=APP_PORT=' + str(main), '--env=METRICS_PORT=' + str(metrics),
                       '--env=DATABASE_URL=postgresql+asyncpg://imagecheck@127.0.0.1:1/imagecheck',
                       '--env=FDS_ENGINE_URL=http://127.0.0.1:1', image], timeout=30)
        cid = own_cid()
        receipt['container_id'] = cid
        check_state(text(call('created', ['container', 'inspect', '--format', INSPECT, cid])),
                    image, cid, owner, running=False)
        call('start', ['start', cid])  # No command or entrypoint override.
        check_state(text(call('running', ['container', 'inspect', '--format', INSPECT, cid])),
                    image, cid, owner, running=True)
        output = call('probe', ['exec', '--user=10001:999', cid, '/venv/bin/python3',
                                '-I', '-c', probe.decode('utf-8'), service], timeout=45)
        receipt['probe'] = check_probe(text(output), service)
        check_state(text(call('after', ['container', 'inspect', '--format', INSPECT, cid])),
                    image, cid, owner, running=True)
    except BaseException:
        primary_failed = True
        raise
    finally:
        try:
            # cidfile may exist even if create timed out after allocating a container.
            if cid is None and cidfile.exists():
                cid = own_cid()
                receipt['container_id'] = cid
            if cid is not None:
                try:
                    call('logs', ['logs', '--tail=200', cid], timeout=10)
                except Exception:
                    cleanup_errors.append('LOG_COLLECTION_FAILED')
                try:
                    call('remove', ['rm', '--force', '--time=5', cid], timeout=15)
                    remaining = text(call('removed', ['ps', '--all', '--no-trunc',
                        '--filter', 'id=' + cid, '--format', '{{.ID}}']))
                    need(not remaining, 'SMOKE_CONTAINER_REMAINS')
                    receipt['cleanup'] = 'REMOVED'
                except Exception:
                    cleanup_errors.append('CONTAINER_REMOVAL_NOT_CONFIRMED')
            else:
                cleanup_errors.append('CREATION_AND_CLEANUP_NOT_CONFIRMED')
        except Exception:
            cleanup_errors.append('CLEANUP_IDENTITY_NOT_CONFIRMED')
        receipt['finished_at'] = datetime.now(timezone.utc).isoformat()
        if cleanup_errors:
            receipt['cleanup_errors'] = cleanup_errors
        if not primary_failed:
            need(not cleanup_errors, 'SMOKE_LOG_OR_CLEANUP_FAILED')
            receipt['status'] = 'PASS'
