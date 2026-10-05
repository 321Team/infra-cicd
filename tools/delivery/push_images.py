#!/usr/bin/env python3
"""Verify archived provenance, rescan, then copy exact OCI images to Harbor.

Default mode is local validation. Execution requires a separately approved
current dispatch context; the historical build run is never relabelled.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import uuid

import delivery_gate as gate
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'trivy'))
from oci_archive import inspect_archive, json_object
from prepare_db import PreflightError, require, runtime_context, utcnow
from scan_images import scan_pair, check_report, check_db
from verify_runner import sha256

SKOPEO = '/usr/bin/skopeo'
SERVICES = gate.SERVICES


def plain(path):
    path = Path(path).absolute()
    for part in (path, *path.parents):
        require(not part.is_symlink(), 'SYMLINK_INPUT')
    info = path.stat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'REGULAR_SINGLE_LINK_REQUIRED')
    return path


def read_json(path):
    path = plain(path)
    require(path.stat().st_size <= 16 * 1024**2, 'JSON_TOO_LARGE')
    raw = path.read_bytes()
    return json_object(raw), hashlib.sha256(raw).hexdigest()


def bind_evidence(record, root, staging):
    """Validate all services before any scanner or registry operation."""
    try:
        verdict = gate.evaluate(record, 'pre_push')
    except (TypeError, ValueError, KeyError):
        raise PreflightError('DELIVERY_INPUT_MALFORMED') from None
    require(verdict['decision'] == 'CONDITIONS_MATCH', 'DELIVERY_GATE_BLOCK')
    app, app_hash = read_json(root / 'result.json')
    build, build_hash = read_json(root / 'build-scan/result.json')
    scan, scan_hash = read_json(root / 'build-scan/scan/result.json')
    require(app_hash == record.get('app_result_sha256'), 'APP_RECEIPT_HASH')
    require(app.get('APP_WORKFLOW') == 'PASS' and
            app.get('APP_TEST_BUILD_SCAN') == 'PASS', 'APP_NOT_PASS')
    require(app.get('build_result_sha256') == build_hash, 'APP_BUILD_HASH')
    require(build.get('BUILD_SCAN') == 'PASS' and
            build.get('scan_result_sha256') == scan_hash, 'BUILD_SCAN_HASH_OR_STATUS')
    require(scan.get('IMAGE_SCAN') == 'PASS' and
            scan_hash == record['build_scan']['scan_result_sha256'], 'SCAN_HASH_OR_STATUS')
    workflow = record['workflow']
    for ctx in (app['context'], build['context'], scan['context']):
        require(str(ctx['run_id']) == str(workflow['run_id']) and
                type(ctx['attempt']) is int and ctx['attempt'] == workflow['run_attempt'] and
                ctx['workflow_sha'] == workflow['commit'] and
                ctx['input_sha256'] == record['build_scan']['input_sha256'], 'BUILD_CONTEXT')
    require(scan['context'] == build['context'], 'SCAN_CONTEXT')
    require(build['source']['commit'] == record['source']['commit'] and
            build['source']['repository'] == record['source']['repository'] and
            scan['source_sha'] == record['source']['commit'], 'SOURCE_BINDING')
    require(set(build['images']) == set(scan['images']) == set(SERVICES), 'IMAGE_SET')
    archives = {}
    for name in SERVICES:
        source = plain(root / 'build-scan' / (name + '.oci.tar'))
        target = staging / (name + '.oci.tar')
        with source.open('rb') as src, target.open('xb') as dst:
            shutil.copyfileobj(src, dst)
        identity = inspect_archive(target)
        for key in ('archive_sha256', 'manifest_digest', 'config_digest'):
            require(identity[key] == build['images'][name][key] ==
                    scan['images'][name][key] == record['build_scan']['images'][name][key],
                    'ARCHIVE_IDENTITY')
        item = scan['images'][name]
        require(item['scan'] == 'PASS' and type(item['exit_code']) is int and
                item['exit_code'] == 0, 'SCAN_ITEM_STATUS')
        report, report_hash = read_json(root / 'build-scan/scan' / name / 'report.json')
        require(report_hash == item['report_sha256'] ==
                record['build_scan']['scan'][name]['report_sha256'], 'REPORT_HASH')
        counts = check_report(report, 0, identity)
        require(counts == item['counts'] == record['build_scan']['scan'][name]['counts'] ==
                {'HIGH': 0, 'CRITICAL': 0}, 'REPORT_FINDINGS')
        archives[name] = target
    return archives


def command(argv, home, capture=False):
    # Neither caller environment nor command error output may disclose credentials.
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(home), 'LC_ALL': 'C',
           'XDG_CONFIG_HOME': str(home / 'config'), 'XDG_RUNTIME_DIR': str(home)}
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen([SKOPEO, *argv], env=env, cwd=home,
                                   stdin=subprocess.DEVNULL, stdout=output if capture else subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            rc = process.wait(timeout=600)
        except BaseException:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            raise
        require(rc == 0, 'REGISTRY_COMMAND_FAILED')
        require(output.tell() <= 4 * 1024**2, 'REGISTRY_OUTPUT_TOO_LARGE')
        output.seek(0)
        return output.read() if capture else b''


def save(folder, result):
    raw = (json.dumps(result, indent=2) + '\n').encode()
    temporary = folder / 'result.pending'
    temporary.write_bytes(raw)
    temporary.replace(folder / 'result.json')
    (folder / 'SHA256SUMS').write_text(hashlib.sha256(raw).hexdigest() + '  result.json\n')


def auth_copy(path, target):
    path = plain(path)
    info = path.stat()
    require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o600,
            'AUTH_OWNER_OR_MODE')
    data, _ = read_json(path)
    # Reject helper execution, registry fallbacks and unrelated registry secrets.
    require(set(data) == {'auths'} and set(data['auths']) == {'10.1.93.54'}, 'AUTH_SCOPE')
    entry = data['auths']['10.1.93.54']
    require(isinstance(entry, dict) and set(entry) == {'auth'} and
            isinstance(entry['auth'], str) and bool(entry['auth']), 'AUTH_FORMAT')
    target.write_text(json.dumps(data))
    target.chmod(0o600)


def execute(record, root, folder, authfile=None, context=None):
    """New private folder required. A failed copy can leave remote blobs/tags."""
    result = {'PUSH': 'NOT_EXECUTED', 'DEPLOY': 'NOT_EXECUTED',
              'DELIVERY_GATE': 'NOT_VERIFIED', 'started_at': utcnow().isoformat(),
              'images': {}, 'build_workflow': record.get('workflow'),
              'execution_context': context, 'error_code': 'NONE'}
    rc = 1
    try:
        with tempfile.TemporaryDirectory(prefix='push-private-', dir=folder.parent) as temp:
            work = Path(temp)
            archives = bind_evidence(record, root, work)
            if context is None:
                result['VALIDATION'] = 'PASS'
                rc = 0
            else:
                approval = record['push_execution']
                require(gate._match(approval.get('reference'), gate.REFERENCE), 'PUSH_APPROVAL_REFERENCE')
                require(approval.get('workflow_sha') == context['workflow_sha'] and
                        str(approval.get('run_id')) == str(context['run_id']) and
                        type(approval.get('attempt')) is int and
                        approval['attempt'] == context['attempt'], 'PUSH_EXECUTION_BINDING')
                require(Path(SKOPEO).is_file(), 'SKOPEO_REQUIRED')
                private_auth = work / 'auth.json'
                auth_copy(authfile, private_auth)
                fresh = folder / 'fresh-scan'
                fresh.mkdir(mode=0o700)
                scan_rc, scan = scan_pair(fresh, context, record['source']['commit'], archives, {})
                require(scan_rc == 0 and scan.get('IMAGE_SCAN') == 'PASS' and
                        scan.get('context') == context and
                        scan.get('source_sha') == record['source']['commit'] and
                        set(scan.get('images', {})) == set(SERVICES), 'FRESH_SCAN_FAILED')
                result['fresh_scan_result_sha256'] = sha256(fresh / 'result.json')
                for name in SERVICES:
                    image = record['build_scan']['images'][name]
                    actual = inspect_archive(archives[name])
                    scanned = scan['images'][name]
                    for key in ('archive_sha256', 'manifest_digest', 'config_digest'):
                        require(actual[key] == image[key] == scanned[key], 'FRESH_SCAN_BINDING')
                    report, digest = read_json(fresh / name / 'report.json')
                    require(digest == scanned['report_sha256'] and
                            scanned.get('scan') == 'PASS' and type(scanned.get('exit_code')) is int and
                            scanned['exit_code'] == 0 and
                            check_report(report, 0, actual) == scanned['counts'] ==
                            {'HIGH': 0, 'CRITICAL': 0}, 'FRESH_REPORT_INVALID')
                # Each unique delivery tag is scoped to this execution, never latest/stable.
                nonce = uuid.uuid4().hex
                for name in SERVICES:
                    image = record['build_scan']['images'][name]
                    repository = gate.REGISTRY_PREFIX + name
                    tag = 'delivery-' + nonce
                    item = {'result': 'UNKNOWN', 'registry_repository': repository,
                            'tag': tag, 'manifest_digest': image['manifest_digest']}
                    result['images'][name] = item
                    result['PUSH'] = 'INCOMPLETE'
                    save(folder, result)  # Preserve target before the first possible remote write.
                    require(sha256(archives[name]) == image['archive_sha256'], 'ARCHIVE_CHANGED')
                    db, _ = read_json(fresh / 'db/result.json')
                    check_db(fresh, db)  # No expiry grace between scan and either copy.
                    command(['copy', '--preserve-digests', '--dest-tls-verify=true',
                             '--dest-authfile', str(private_auth),
                             'oci-archive:' + str(archives[name]),
                             'docker://' + repository + ':' + tag], work)
                    for reference in (repository + ':' + tag, repository + '@' + image['manifest_digest']):
                        raw = command(['inspect', '--raw', '--tls-verify=true', '--authfile',
                                       str(private_auth), 'docker://' + reference], work, capture=True)
                        require('sha256:' + hashlib.sha256(raw).hexdigest() == image['manifest_digest'],
                                'REMOTE_MANIFEST_MISMATCH')
                    item['result'] = 'PASS'
                    save(folder, result)
                result['PUSH'], rc = 'PASS', 0
    except PreflightError as exc:
        result['error_code'] = str(exc)
        rc = 1
        if result['PUSH'] == 'PASS':
            result['PUSH'] = 'INCOMPLETE'
    except (Exception, KeyboardInterrupt):
        # Never serialize exceptions containing paths, credentials or server responses.
        result['error_code'] = 'PUSH_INTERRUPTED_OR_INPUT_IO_ERROR'
        rc = 1
        if result['PUSH'] == 'PASS':
            result['PUSH'] = 'INCOMPLETE'
    result['finished_at'] = utcnow().isoformat()
    save(folder, result)  # Failure to seal must propagate, never return a success.
    return rc, result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--record', required=True, type=Path)
    parser.add_argument('--record-sha256', required=True)
    parser.add_argument('--evidence-root', required=True, type=Path)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--authfile', type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        record, digest = read_json(args.record)
        require(re.fullmatch('[0-9a-f]{64}', args.record_sha256) and
                digest == args.record_sha256, 'REVIEWED_RECORD_HASH')
        context = runtime_context(os.environ) if args.execute else None
        if args.execute:
            dirty = subprocess.check_output(
                ['/usr/bin/git', 'status', '--porcelain', '--untracked-files=all'],
                timeout=15, stderr=subprocess.DEVNULL)
            require(not dirty.strip(), 'CLEAN_CHECKOUT_REQUIRED')
        require(not args.execute or args.authfile is not None, 'AUTHFILE_REQUIRED')
        home = Path.home()
        fd = os.open(home / 'FDS_CICD_Push.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'r+') as lock:
            info = os.fstat(lock.fileno())
            require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and
                    info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600, 'LOCK_INVALID')
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            folder = Path(tempfile.mkdtemp(prefix='FDS_CICD_Push.', dir=home))
            signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
            print('EVIDENCE_DIR=' + str(folder), flush=True)
            rc, result = execute(record, args.evidence_root, folder, args.authfile, context)
            print('PUSH=' + result['PUSH'] + '\nERROR_CODE=' + result['error_code'])
            return rc
    except (Exception, KeyboardInterrupt):
        print('PUSH=NOT_CONFIRMED\nERROR_CODE=INPUT_CONTEXT_OR_RECEIPT_ERROR')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
