#!/usr/bin/env python3
"""Collect/check bound app receipts. Evidence integrity never authorizes delivery."""
import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import tarfile

import build_scan as flow
from prepare_db import timestamp, validate_metadata, DB_REPOSITORY
from scan_images import check_report
from verify_runner import BINARY_SHA256

SCHEMA = 'P1-CICD-app-evidence-1'
JSON_LIMIT = 4 * 1024**2
PACKAGE_LIMIT = 32 * 1024**2
TAR_LIMIT = PACKAGE_LIMIT + 64 * 1024
FILE_LIMIT = 4 * 1024**3
RECEIPTS = {'result.json', 'handoff.json', 'build-scan/result.json',
            'build-scan/scan/result.json', 'build-scan/scan/db/result.json',
            'build-scan/scan/db/cache/db/metadata.json'}
HEX = r'[0-9a-f]{64}'


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def expected_input(data):
    flow.require(isinstance(data, dict) and set(data) ==
        {'workflow_sha', 'source_sha', 'run_id', 'attempt', 'input_sha256'}, 'EXPECTED_FIELDS_REQUIRED')
    for key in ('workflow_sha', 'source_sha'):
        flow.require(isinstance(data[key], str) and re.fullmatch('[0-9a-f]{40}', data[key]), 'EXPECTED_SHA_INVALID')
    flow.require(isinstance(data['input_sha256'], str) and re.fullmatch(HEX, data['input_sha256']),
                 'EXPECTED_INPUT_HASH_INVALID')
    flow.require(isinstance(data['run_id'], str) and re.fullmatch('[1-9][0-9]*', data['run_id']) and
                 type(data['attempt']) is int and data['attempt'] > 0, 'EXPECTED_RUN_INVALID')
    return data


def safe_file(path, limit, retain=False):
    flow.offline.plain_path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        flow.require(stat.S_ISREG(before.st_mode) and before.st_size <= limit, 'FILE_TYPE_OR_SIZE')
        value, chunks, total = hashlib.sha256(), [], 0
        while chunk := stream.read(1024**2):
            total += len(chunk)
            flow.require(total <= limit, 'FILE_TOO_LARGE')
            value.update(chunk)
            if retain:
                chunks.append(chunk)
        after = os.fstat(stream.fileno())
        flow.require((before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
                     (after.st_size, after.st_mtime_ns, after.st_ctime_ns) and total == before.st_size,
                     'FILE_CHANGED_DURING_READ')
    return {'sha256': value.hexdigest(), 'size': total}, b''.join(chunks)


class Local:
    def __init__(self, root):
        self.root, self.receipts, self.payloads = root, {}, {}
        flow.offline.plain_path(root)

    def read(self, name, sha=None):
        flow.require(name in RECEIPTS, 'RECEIPT_PATH_INVALID')
        info, raw = safe_file(self.root / name, JSON_LIMIT, retain=True)
        flow.require(sha is None or info['sha256'] == sha, 'RECEIPT_HASH_MISMATCH:' + name)
        self.receipts[name] = raw
        return flow.json_object(raw)

    def payload(self, name, sha, kind, identity=None, scan=None):
        flow.relative(name)
        flow.require(isinstance(sha, str) and re.fullmatch(HEX, sha), 'PAYLOAD_HASH_INVALID')
        info, _ = safe_file(self.root / name, FILE_LIMIT)
        flow.require(info['sha256'] == sha, 'PAYLOAD_HASH_MISMATCH:' + name)
        if identity is not None:
            actual = flow.inspect_archive(self.root / name)
            flow.require(all(actual.get(k) == identity.get(k) for k in
                ('archive_sha256', 'manifest_digest', 'config_digest', 'diff_ids')), 'OCI_RECEIPT_MISMATCH')
        if scan is not None:
            _, raw = safe_file(self.root / name, 64 * 1024**2, retain=True)
            flow.require(digest(raw) == sha, 'REPORT_CHANGED')
            counts = check_report(flow.json_object(raw), scan['exit_code'], scan)
            flow.require(counts == scan['counts'], 'REPORT_COUNTS_MISMATCH')
        entry = {**info, 'kind': kind}
        flow.require(name not in self.payloads or self.payloads[name] == entry, 'DUPLICATE_PAYLOAD_MISMATCH')
        self.payloads[name] = entry


class Submitted:
    def __init__(self, files, manifest):
        self.files, self.manifest = files, manifest
        self.receipts, self.payloads = {}, {}

    def read(self, name, sha=None):
        flow.require(name in RECEIPTS and name in self.files, 'RECEIPT_MISSING:' + name)
        raw = self.files[name]
        flow.require(sha is None or digest(raw) == sha, 'RECEIPT_HASH_MISMATCH:' + name)
        self.receipts[name] = raw
        return flow.json_object(raw)

    def payload(self, name, sha, kind, identity=None, scan=None):
        # Payload is deliberately omitted. This checks the submitted local-check record only.
        entry = self.manifest['payloads'].get(name)
        flow.require(isinstance(entry, dict) and set(entry) == {'sha256', 'size', 'kind'} and
                     entry['sha256'] == sha and entry['kind'] == kind and
                     type(entry['size']) is int and 0 <= entry['size'] <= FILE_LIMIT,
                     'SUBMITTED_PAYLOAD_RECORD_MISMATCH:' + name)
        self.payloads[name] = entry


def time_range(child, parent=None):
    start, end = timestamp(child['started_at']), timestamp(child['finished_at'])
    flow.require(start <= end, 'RECEIPT_TIME_ORDER')
    if parent:
        flow.require(timestamp(parent['started_at']) <= start <= end <= timestamp(parent['finished_at']),
                     'RECEIPT_TIME_BINDING')


def steps(provider, record, prefix, require_success=False):
    values = record.get('steps')
    flow.require(isinstance(values, list) and len(values) <= 512, 'STEP_RECORDS_INVALID')
    names = set()
    for item in values:
        name = item.get('name')
        flow.require(isinstance(name, str) and re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]{0,160}', name)
                     and name not in names and type(item.get('exit_code')) is int, 'STEP_RECORD_INVALID')
        names.add(name)
        if require_success:
            flow.require(item['exit_code'] == 0, 'SUCCESS_WITH_FAILED_STEP')
        provider.payload(prefix + name + '.log', item['log_sha256'], 'command-log')
    return names


def identity_shape(image):
    flow.require(isinstance(image, dict) and re.fullmatch(HEX, image.get('archive_sha256', '')) and
                 re.fullmatch('sha256:' + HEX, image.get('manifest_digest', '')) and
                 re.fullmatch('sha256:' + HEX, image.get('config_digest', '')) and
                 isinstance(image.get('diff_ids'), list) and 0 < len(image['diff_ids']) <= 128 and
                 all(isinstance(v, str) and re.fullmatch('sha256:' + HEX, v) for v in image['diff_ids']),
                 'IMAGE_IDENTITY_INVALID')


def check(provider, expected):
    expected_input(expected)
    top = provider.read('result.json')
    context = top['context']
    for key in ('workflow_sha', 'run_id', 'attempt', 'input_sha256'):
        flow.require(type(context.get(key)) is type(expected[key]) and context[key] == expected[key],
                     'EXPECTED_CONTEXT_MISMATCH:' + key)
    flow.require(context.get('host') == 'cicd-runner01' and context.get('user') == 'server01_cicd-runner'
                 and type(context.get('uid')) is int and context['uid'] == 1000 and
                 context.get('run_url') == 'https://github.com/321Team/infra-cicd/actions/runs/' + expected['run_id'],
                 'RUNNER_CONTEXT_MISMATCH')
    outcome = top.get('APP_WORKFLOW')
    flow.require(outcome in ('PASS', 'BLOCK', 'FAIL'), 'WORKFLOW_OUTCOME_INVALID')
    flow.require(top.get('PUSH') == top.get('DEPLOY') == 'NOT_EXECUTED' and
                 top.get('DELIVERY_GATE') == 'NOT_VERIFIED', 'DELIVERY_SCOPE_MISMATCH')
    time_range(top)
    complete = outcome in ('PASS', 'BLOCK')
    top_steps = steps(provider, top, '', complete)
    if complete:
        flow.require({'source-init', 'source-origin-set', 'source-fetch', 'source-fetched', 'source-head-set',
                      'handoff-head', 'handoff-origin', 'handoff-tree'} <= top_steps, 'SOURCE_STEPS_MISSING')
    handoff = None
    if 'handoff_sha256' in top:
        handoff = provider.read('handoff.json', top['handoff_sha256'])
        source = handoff.get('source', {})
        flow.require(handoff.get('HANDOFF') == top.get('HANDOFF') == 'PASS' and
                     source.get('repository') == flow.SOURCE_REPOSITORY and
                     source.get('commit') == expected['source_sha'], 'HANDOFF_SOURCE_MISMATCH')
        flow.require(isinstance(source.get('approved_by'), str) and source['approved_by'].strip() and
                     isinstance(source.get('approval_reference'), str) and
                     source['approval_reference'].startswith('https://github.com/'), 'SOURCE_DECLARATION_MISSING')
        files = handoff['files']
        wanted = {n + '/' + f for n in flow.SERVICES for f in
                  ('Dockerfile', 'requirements.txt', 'requirements-test.txt', 'requirements-runtime.lock',
                   'requirements-test.lock')}
        flow.require(set(files) == wanted and set(handoff['test_scope']) == set(flow.SERVICES), 'HANDOFF_SCOPE_INVALID')
        for path, item in files.items():
            label = 'handoff-' + path.replace('/', '-')
            flow.require(label in top_steps, 'HANDOFF_BLOB_STEP_MISSING')
            provider.payload(label + '.log', item['sha256'], 'command-log')
        for scope in handoff['test_scope'].values():
            flow.validate_unit(scope['unit'])
            flow.test_step(scope['contract'])
        flow.require(any(not flow.unit_not_applicable(s['unit']) for s in handoff['test_scope'].values()),
                     'UNIT_PHASE_REQUIRED')
    built = scan = None
    if 'build_result_sha256' in top:
        flow.require(handoff is not None, 'BUILD_WITHOUT_HANDOFF')
        built = provider.read('build-scan/result.json', top['build_result_sha256'])
        build_context = {**context, 'handoff_sha256': top['handoff_sha256']}
        flow.require(built.get('context') == build_context and built.get('source') == handoff['source'] and
                     built.get('BUILD_SCAN') == outcome == top.get('APP_TEST_BUILD_SCAN'), 'BUILD_BINDING_MISMATCH')
        flow.require(built.get('PUSH') == built.get('DEPLOY') == 'NOT_EXECUTED' and
                     built.get('DELIVERY_GATE') == 'NOT_VERIFIED', 'DELIVERY_SCOPE_MISMATCH')
        time_range(built, top)
        build_steps = steps(provider, built, 'build-scan/', complete)
        if 'source_archive_sha256' in built:
            provider.payload('build-scan/source.tar', built['source_archive_sha256'], 'source-archive')
        elif complete:
            raise flow.PreflightError('SOURCE_ARCHIVE_HASH_MISSING')
        if complete:
            required = {'source-head', 'source-origin', 'source-tree', 'source-archive', 'podman-version',
                        'podman-rootless', 'offline-base-load', 'offline-base-tag', 'offline-base-resolve'}
            na = []
            for name, scope in handoff['test_scope'].items():
                required.update(name + '-' + suffix for suffix in
                    ('offline-venv', 'offline-install', 'offline-check', 'contract-0', 'build', 'save'))
                if flow.unit_not_applicable(scope['unit']):
                    na.append({'service': name, 'phase': 'unit', **scope['unit']})
                else:
                    required.add(name + '-unit-0')
            flow.require(required <= build_steps and built.get('not_applicable_tests') == na,
                         'TEST_BUILD_SCOPE_MISMATCH')
            supply = built.get('offline_supply', {})
            flow.require(supply.get('SUPPLY_COPY') == supply.get('REPOSITORY_DIGEST_RESOLUTION') == 'PASS',
                         'OFFLINE_SUPPLY_NOT_VERIFIED')
        images = built.get('images', {})
        flow.require(isinstance(images, dict) and set(images) <= set(flow.SERVICES), 'BUILD_IMAGES_INVALID')
        if complete:
            flow.require(set(images) == set(flow.SERVICES), 'TWO_BUILD_IMAGES_REQUIRED')
        for name, image in images.items():
            identity_shape(image)
            flow.require(image.get('image_id') == image['config_digest'], 'BUILD_CONFIG_MISMATCH')
            provider.payload('build-scan/' + name + '.oci.tar', image['archive_sha256'], 'build-oci', identity=image)
        if 'scan_result_sha256' in built:
            scan = provider.read('build-scan/scan/result.json', built['scan_result_sha256'])
            flow.require(scan.get('IMAGE_SCAN') in ('PASS', 'BLOCK', 'FAIL'), 'SCAN_OUTCOME_INVALID')
            flow.require(scan.get('context') == build_context and scan.get('source_sha') == expected['source_sha'],
                         'SCAN_BINDING_MISMATCH')
            flow.require(scan.get('binary_sha256') == BINARY_SHA256 and
                         scan.get('push') == scan.get('deploy') == 'NOT_EXECUTED' and
                         scan.get('DELIVERY_GATE') == 'NOT_VERIFIED', 'SCAN_SCOPE_MISMATCH')
            time_range(scan, built)
            scanned = scan.get('images', {})
            flow.require(isinstance(scanned, dict) and set(scanned) == set(flow.SERVICES[:len(scanned)]),
                         'SCAN_PREFIX_MISMATCH')
            statuses = {name: 'NOT_SCANNED' for name in flow.SERVICES}
            for name, image in scanned.items():
                identity_shape(image)
                flow.require(name in images and all(image[k] == images[name][k] for k in
                    ('archive_sha256', 'manifest_digest', 'config_digest', 'diff_ids')), 'SCAN_BUILD_IDENTITY_MISMATCH')
                counts = image.get('counts', {})
                flow.require(set(counts) == {'HIGH', 'CRITICAL'} and all(type(n) is int and n >= 0 for n in counts.values()),
                             'SCAN_COUNTS_INVALID')
                status = 'BLOCK' if sum(counts.values()) else 'PASS'
                flow.require(image.get('scan') == status and type(image.get('exit_code')) is int and
                             image['exit_code'] == (10 if status == 'BLOCK' else 0), 'SCAN_STATUS_MISMATCH')
                statuses[name] = status
                provider.payload('build-scan/scan/' + name + '.oci.tar', image['archive_sha256'], 'scan-oci', identity=image)
                provider.payload('build-scan/scan/' + name + '/report.json', image['report_sha256'], 'trivy-report', scan=image)
            if complete:
                flow.require(scan.get('IMAGE_SCAN') == outcome and built.get('scan_status') == statuses,
                             'SCAN_OUTCOME_MISMATCH')
                if outcome == 'PASS':
                    flow.require(len(scanned) == 2 and all(s == 'PASS' for s in statuses.values()), 'SCAN_PASS_INCOMPLETE')
                else:
                    values = [statuses[n] for n in flow.SERVICES[:len(scanned)]]
                    flow.require(values and values[-1] == 'BLOCK' and all(s == 'PASS' for s in values[:-1]) and
                                 scan.get('error_code') == 'HIGH_CRITICAL_FINDINGS', 'SCAN_BLOCK_INVALID')
            if 'db_result_sha256' in scan:
                db = provider.read('build-scan/scan/db/result.json', scan['db_result_sha256'])
                flow.require(db.get('context') == build_context and db.get('binary_sha256') == BINARY_SHA256 and
                             db.get('db_repository') == DB_REPOSITORY, 'DB_BINDING_MISMATCH')
                time_range(db, scan)
                if db.get('DB_PREFLIGHT') == 'PASS':
                    metadata = provider.read('build-scan/scan/db/cache/db/metadata.json', db['metadata_sha256'])
                    provider.payload('build-scan/scan/db/cache/db/trivy.db', db['db_sha256'], 'trivy-db')
                    normalized = validate_metadata(metadata, timestamp(db['validated_at']))
                    flow.require(normalized == db['metadata'], 'DB_METADATA_MISMATCH')
                    if complete:
                        validate_metadata(metadata, timestamp(scan['finished_at']))
                elif complete:
                    raise flow.PreflightError('DB_NOT_PASS')
            elif complete:
                raise flow.PreflightError('DB_RECEIPT_MISSING')
        elif complete:
            raise flow.PreflightError('SCAN_RECEIPT_MISSING')
    else:
        flow.require(not complete and top.get('APP_TEST_BUILD_SCAN') == 'NOT_EXECUTED', 'BUILD_RECEIPT_MISSING')
    return {'EVIDENCE_CHECK': 'PASS', 'RECORDED_APP_WORKFLOW': outcome,
            'BINDING': 'COMPLETE' if complete else 'PARTIAL_FAILURE_RECORD',
            'SOURCE_BINDING': 'MATCH' if handoff else 'UNAVAILABLE_BEFORE_HANDOFF',
            'RAW_PAYLOAD_CHECK': 'LOCAL_BYTES' if isinstance(provider, Local) else 'NOT_INCLUDED_SERVER_RECORD_ONLY',
            'SOURCE_APPROVAL': 'NOT_INDEPENDENTLY_VERIFIED', 'DELIVERY_GATE': 'NOT_VERIFIED'}


def collect(root, output, expected):
    provider = Local(root)
    checked = check(provider, expected)
    flow.offline.plain_path(output.parent)
    flow.require(not os.path.lexists(output), 'OUTPUT_ALREADY_EXISTS')
    # Recheck referenced bytes before writing; never modify the source run or old output.
    for name, raw in provider.receipts.items():
        info, _ = safe_file(root / name, JSON_LIMIT)
        flow.require(info['sha256'] == digest(raw), 'RECEIPT_CHANGED')
    for name, item in provider.payloads.items():
        info, _ = safe_file(root / name, FILE_LIMIT)
        flow.require(info['sha256'] == item['sha256'], 'PAYLOAD_CHANGED')
    manifest = {'schema': SCHEMA, 'tool_sha256': flow.sha256(Path(__file__)),
                'receipts': {n: {'sha256': digest(raw), 'size': len(raw)} for n, raw in provider.receipts.items()},
                'payloads': provider.payloads, 'raw_payloads_included': False}
    flow.require(len(provider.payloads) <= 1024, 'TOO_MANY_PAYLOADS')
    files = {**provider.receipts, 'manifest.json': (json.dumps(manifest, sort_keys=True, indent=2) + '\n').encode()}
    flow.require(sum(len(v) for v in files.values()) <= PACKAGE_LIMIT, 'PACKAGE_TOO_LARGE')
    output.mkdir(mode=0o700)
    archive = output / 'app-evidence.tar.gz'
    with archive.open('xb') as stream, gzip.GzipFile(fileobj=stream, mode='wb', mtime=0) as gz, \
            tarfile.open(fileobj=gz, mode='w') as tar:
        for name, raw in sorted(files.items()):
            member = tarfile.TarInfo(name)
            member.size, member.mode = len(raw), 0o600
            tar.addfile(member, io.BytesIO(raw))
    checksum = flow.sha256(archive)
    (output / 'app-evidence.tar.gz.sha256').write_text(checksum + '  app-evidence.tar.gz\n')
    return archive, checksum, checked


def verify(archive, checksum, expected):
    flow.require(isinstance(checksum, str) and re.fullmatch(HEX, checksum), 'ARCHIVE_SHA_REQUIRED')
    info, raw = safe_file(archive, PACKAGE_LIMIT, retain=True)
    flow.require(info['sha256'] == checksum, 'ARCHIVE_HASH_MISMATCH')
    # Bound decoded bytes before tarfile consumes PAX/GNU metadata or padding.
    with gzip.GzipFile(fileobj=io.BytesIO(raw), mode='rb') as gz:
        decoded = gz.read(TAR_LIMIT + 1)
    flow.require(len(decoded) <= TAR_LIMIT, 'DECOMPRESSED_PACKAGE_TOO_LARGE')
    files, total = {}, 0
    with tarfile.open(fileobj=io.BytesIO(decoded), mode='r:') as tar:
        for member in tar:
            flow.require(member.isfile() and member.name in RECEIPTS | {'manifest.json'} and
                         member.name not in files and 0 <= member.size <= JSON_LIMIT, 'ARCHIVE_MEMBER_INVALID')
            total += member.size
            flow.require(total <= PACKAGE_LIMIT, 'PACKAGE_TOO_LARGE')
            files[member.name] = tar.extractfile(member).read()
    manifest = flow.json_object(files.pop('manifest.json'))
    flow.require(set(manifest) == {'schema', 'tool_sha256', 'receipts', 'payloads', 'raw_payloads_included'} and
                 manifest['schema'] == SCHEMA and manifest['raw_payloads_included'] is False and
                 re.fullmatch(HEX, manifest['tool_sha256']), 'MANIFEST_INVALID')
    flow.require(isinstance(manifest['receipts'], dict) and set(manifest['receipts']) == set(files) and
                 isinstance(manifest['payloads'], dict) and len(manifest['payloads']) <= 1024, 'MANIFEST_FILES_INVALID')
    for name, value in files.items():
        flow.require(manifest['receipts'][name] == {'sha256': digest(value), 'size': len(value)}, 'MEMBER_HASH_MISMATCH')
    provider = Submitted(files, manifest)
    result = check(provider, expected)
    flow.require(set(provider.receipts) == set(files) and provider.payloads == manifest['payloads'],
                 'UNBOUND_MANIFEST_CONTENT')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='mode', required=True)
    collect_parser = sub.add_parser('collect')
    collect_parser.add_argument('--run-dir', required=True, type=Path)
    collect_parser.add_argument('--output-dir', required=True, type=Path)
    verify_parser = sub.add_parser('verify')
    verify_parser.add_argument('--archive', required=True, type=Path)
    verify_parser.add_argument('--sha256', required=True)
    for child in (collect_parser, verify_parser):
        child.add_argument('--expected', required=True, type=Path)
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        _, raw = safe_file(args.expected, JSON_LIMIT, retain=True)
        expected = expected_input(flow.json_object(raw))
        if args.mode == 'collect':
            archive, checksum, result = collect(args.run_dir.absolute(), args.output_dir.absolute(), expected)
            print('ARCHIVE=' + str(archive) + '\nSHA256=' + checksum)
        else:
            result = verify(args.archive.absolute(), args.sha256, expected)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (flow.PreflightError, OSError, ValueError, TypeError, KeyError, AttributeError,
            RecursionError, EOFError, tarfile.TarError):
        # Avoid arbitrary raw JSON values, report contents or paths in error output.
        print('EVIDENCE_CHECK=FAIL\nDELIVERY_GATE=NOT_VERIFIED')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
