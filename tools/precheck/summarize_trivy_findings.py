#!/usr/bin/env python3
"""One-shot informational report collector; never authorizes a security gate.

Read one existing Trivy JSON report and create one new summary file. No network,
subprocess, scanner, installation, or operating-system configuration changes.
Report hashes identify collected bytes; they do not verify image provenance.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

LIMIT = 64 * 1024 * 1024
MAX_ROWS = 100000
SAFE_TEXT = re.compile(r"[A-Za-z0-9_.+:/@~<>=, ()*\-]{0,256}\Z")
VULN_ID = re.compile(r"(?:CVE-[0-9]{4}-[0-9]{4,}|GHSA-[a-z0-9-]+)\Z")


class InputError(Exception):
    pass


def require(condition):
    if not condition:
        raise InputError()


def regular_parents(path):
    for parent in path.parents:
        require(stat.S_ISDIR(parent.lstat().st_mode))


def read_report(path):
    path = path.absolute()
    regular_parents(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_size <= LIMIT)
        raw = stream.read(LIMIT + 1)
        after = os.fstat(stream.fileno())
        require(len(raw) <= LIMIT and len(raw) == before.st_size)
        require((before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
                (after.st_size, after.st_mtime_ns, after.st_ctime_ns))
    return raw


def text(value):
    require(isinstance(value, str) and SAFE_TEXT.fullmatch(value) is not None)
    return value


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


def summarize(raw):
    require(len(raw) <= LIMIT)
    report = json.loads(raw, object_pairs_hook=unique_object)
    require(isinstance(report, dict) and type(report.get('SchemaVersion')) is int)
    require(report['SchemaVersion'] == 2 and report.get('ArtifactType') == 'container_image')
    results = report.get('Results')
    require(isinstance(results, list) and len(results) <= 10000)
    rows = []
    records_seen = 0
    for index, result in enumerate(results):
        require(isinstance(result, dict))
        vulnerabilities = result.get('Vulnerabilities')
        if vulnerabilities is None:
            vulnerabilities = []
        require(isinstance(vulnerabilities, list))
        records_seen += len(vulnerabilities)
        require(records_seen <= MAX_ROWS)
        for item in vulnerabilities:
            require(isinstance(item, dict))
            if item.get('Severity') not in ('HIGH', 'CRITICAL'):
                continue
            identifier = text(item.get('VulnerabilityID'))
            require(VULN_ID.fullmatch(identifier) is not None)
            rows.append({
                'result_index': index,
                'class': text(result.get('Class', '')),
                'type': text(result.get('Type', '')),
                'id': identifier,
                'package': text(item.get('PkgName')),
                'installed_version': text(item.get('InstalledVersion')),
                'fixed_version_reported': text(item.get('FixedVersion', '')),
                'severity': item['Severity'],
                'status_reported': text(item.get('Status', '')),
            })
    rows.sort(key=lambda r: (r['severity'], r['id'], r['package'], r['result_index']))
    return {
        'schema': 'P1-CICD-trivy-findings-summary-1',
        'scope': 'INFORMATION_ONLY_NOT_A_GATE_OR_APPROVAL',
        'source_report_sha256': hashlib.sha256(raw).hexdigest(),
        'source_report_bytes': len(raw),
        'finding_records': {level: sum(r['severity'] == level for r in rows)
                            for level in ('CRITICAL', 'HIGH')},
        'unique_vulnerability_ids': len({r['id'] for r in rows}),
        'findings': rows,
        'limits': ['Report identity, scanner success and image binding are not verified.',
                   'Fixed versions and statuses are report claims, not upgrade approval.',
                   'Zero extracted findings is not a Scan PASS or Delivery PASS.'],
    }


def write_new(path, summary):
    path = path.absolute()
    regular_parents(path)
    raw = (json.dumps(summary, ensure_ascii=True, indent=2) + '\n').encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        write_new(args.output, summarize(read_report(args.report)))
    except (OSError, ValueError, TypeError, RecursionError, InputError):
        print('FINDINGS_SUMMARY=FAIL\nERROR_CODE=INPUT_OR_OUTPUT_ERROR', file=sys.stderr)
        return 1
    print('FINDINGS_SUMMARY=COLLECTED\nASSESSMENT=INFORMATION_ONLY')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
