"""Consume reviewed local OCI/wheels in a new run directory; no downloader.

Hash checks bind bytes to the input record, not approval or remote attestation.
The private copies are not a sandbox against other processes with the same UID.
"""
from email.parser import BytesParser
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools' / 'trivy'))
from oci_archive import inspect_archive, json_object  # noqa: E402
from prepare_db import require, unique_object  # noqa: E402
from verify_runner import sha256  # noqa: E402

SERVICES = ('transaction-api', 'fds-engine')
PROFILES = tuple(s + '-' + m for s in SERVICES for m in ('runtime', 'test'))
HEX = r'[0-9a-f]{64}'
NAME = r'[a-z0-9][a-z0-9-]*'
VERSION = r'[A-Za-z0-9_.+!-]+'
WHEEL = r'[A-Za-z0-9_.+!-]+\.whl'
LIMIT = 1024 * 1024 * 1024


def validate_record(record):
    require(isinstance(record, dict) and set(record) == {
        'directory', 'archive_sha256', 'manifest_digest', 'config_digest',
        'profiles', 'requirements'}, 'OFFLINE_RECORD_FIELDS')
    directory = record['directory']
    require(isinstance(directory, str) and re.fullmatch(r'/[A-Za-z0-9_./-]+', directory)
            and all(p not in ('', '.', '..') for p in directory.split('/')[1:]),
            'OFFLINE_DIRECTORY_INVALID')
    for key in ('archive_sha256', 'manifest_digest', 'config_digest'):
        pattern = HEX if key == 'archive_sha256' else 'sha256:' + HEX
        require(isinstance(record[key], str) and re.fullmatch(pattern, record[key]),
                'OFFLINE_IDENTITY_REQUIRED')
    require(isinstance(record['profiles'], dict) and set(record['profiles']) == set(PROFILES),
            'OFFLINE_EXACT_FOUR_PROFILES')
    for profile in record['profiles'].values():
        require(isinstance(profile, dict) and set(profile) == {
            'roots_sha256', 'lock_sha256', 'inventory_sha256'}, 'OFFLINE_PROFILE_FIELDS')
        require(all(isinstance(v, str) and re.fullmatch(HEX, v) for v in profile.values()),
                'OFFLINE_PROFILE_HASH_REQUIRED')
    paths = {s + '/' + f for s in SERVICES for f in ('requirements.txt', 'requirements-test.txt')}
    require(isinstance(record['requirements'], dict) and set(record['requirements']) == paths
            and all(isinstance(v, str) and re.fullmatch(HEX, v)
                    for v in record['requirements'].values()), 'OFFLINE_REQUIREMENTS_HASH_REQUIRED')
    return record


def plain_path(path):
    # Reject symlinks in every component, including the wheelhouse directory.
    require(path.is_absolute() and path.resolve(strict=True) == path, 'SUPPLY_PATH_LINK')


def copy_checked(source, target, digest, maximum):
    plain_path(source)
    descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as src:
        before = os.fstat(src.fileno())
        require(stat.S_ISREG(before.st_mode) and 0 < before.st_size <= maximum,
                'SUPPLY_FILE_TYPE_OR_SIZE')
        total, checksum = 0, hashlib.sha256()
        with target.open('xb') as dst:
            while chunk := src.read(1024 * 1024):
                total += len(chunk)
                require(total <= maximum, 'SUPPLY_FILE_TOO_LARGE')
                checksum.update(chunk)
                dst.write(chunk)
        after = os.fstat(src.fileno())
    signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    require(signature(before) == signature(after) and total == before.st_size,
            'SUPPLY_FILE_CHANGED')
    require(checksum.hexdigest() == digest, 'SUPPLY_FILE_HASH_MISMATCH')
    return total


def wheel_identity(path, row):
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        require(len(entries) <= 20000 and len({e.filename for e in entries}) == len(entries)
                and sum(e.file_size for e in entries) <= 512 * 1024**2, 'WHEEL_SIZE_OR_ENTRIES')
        require(all(not e.filename.startswith('/') and '\\' not in e.filename
                    and '..' not in e.filename.split('/') and not e.flag_bits & 1
                    and (e.external_attr >> 16) & 0o170000 != 0o120000 for e in entries),
                'WHEEL_PATH_OR_LINK')
        metadata = [e for e in entries if re.fullmatch(r'[^/]+\.dist-info/METADATA', e.filename)]
        require(len(metadata) == 1 and metadata[0].file_size <= 2 * 1024**2, 'WHEEL_METADATA')
        value = BytesParser().parsebytes(archive.read(metadata[0]))
    require(len(value.get_all('Name', [])) == len(value.get_all('Version', [])) == 1,
            'WHEEL_IDENTITY')
    require(re.sub(r'[-_.]+', '-', value['Name']).lower() == row['name']
            and value['Version'] == row['version'], 'WHEEL_IDENTITY_MISMATCH')
    for dependency in value.get_all('Requires-Dist', []):
        require('@' not in dependency and '://' not in dependency and '\\' not in dependency
                and '\n' not in dependency and '\r' not in dependency,
                'WHEEL_DIRECT_DEPENDENCY_UNSUPPORTED')


def inventory_and_lock(folder):
    inventory = json.loads((folder / 'inventory.json').read_bytes(), object_pairs_hook=unique_object)
    require(isinstance(inventory, list) and 0 < len(inventory) <= 256, 'WHEEL_INVENTORY_COUNT')
    records, filenames = {}, set()
    for row in inventory:
        require(isinstance(row, dict) and set(row) == {'name', 'version', 'file', 'sha256'},
                'WHEEL_INVENTORY_FIELDS')
        for key, pattern in (('name', NAME), ('version', VERSION), ('file', WHEEL), ('sha256', HEX)):
            require(isinstance(row[key], str) and re.fullmatch(pattern, row[key]),
                    'WHEEL_INVENTORY_VALUE')
        require(row['name'] not in records and row['file'] not in filenames, 'WHEEL_DUPLICATE')
        require(re.sub(r'[-_.]+', '-', row['file'].split('-')[0]).lower() == row['name'],
                'WHEEL_FILENAME_IDENTITY')
        records[row['name']] = row
        filenames.add(row['file'])
    extras = {}
    for root in (folder / 'roots.txt').read_text().splitlines():
        match = re.fullmatch(r'([A-Za-z0-9][A-Za-z0-9._-]*)(\[[A-Za-z0-9_,.-]+\])?==(' + VERSION + ')', root)
        require(match is not None, 'ROOT_REQUIREMENT_FORMAT')
        name = re.sub(r'[-_.]+', '-', match[1]).lower()
        require(name not in extras and name in records and records[name]['version'] == match[3],
                'ROOT_REQUIREMENT_MISMATCH')
        extras[name] = match[2] or ''
    expected = ''.join(name + extras.get(name, '') + '==' + row['version'] +
                       ' --hash=sha256:' + row['sha256'] + '\n' for name, row in sorted(records.items()))
    require((folder / 'requirements.lock').read_bytes() == expected.encode(), 'LOCK_INVENTORY_MISMATCH')
    return inventory


def stage(record, target):
    validate_record(record)
    source = Path(record['directory'])
    plain_path(source)
    target.mkdir(mode=0o700)  # Existing directory is never reused or cleaned.
    total = copy_checked(source / 'base.oci.tar', target / 'base.oci.tar', record['archive_sha256'], LIMIT)
    identity = inspect_archive(target / 'base.oci.tar')
    require(identity['manifest_digest'] == record['manifest_digest']
            and identity['config_digest'] == record['config_digest'], 'SUPPLY_BASE_IDENTITY_MISMATCH')
    for name in PROFILES:
        folder = target / name
        folder.mkdir(mode=0o700)
        for filename, key in (('roots.txt', 'roots_sha256'), ('requirements.lock', 'lock_sha256'),
                              ('inventory.json', 'inventory_sha256')):
            copy_checked(source / name / filename, folder / filename, record['profiles'][name][key], 1024**2)
        inventory = inventory_and_lock(folder)
        wheels = source / name / 'wheels'
        plain_path(wheels)
        require({p.name for p in wheels.iterdir()} == {r['file'] for r in inventory}, 'WHEELHOUSE_INVENTORY_MISMATCH')
        (folder / 'wheels').mkdir(mode=0o700)
        for row in inventory:
            destination = folder / 'wheels' / row['file']
            total += copy_checked(wheels / row['file'], destination, row['sha256'], 64 * 1024**2)
            require(total <= LIMIT, 'SUPPLY_TOTAL_SIZE_LIMIT')
            wheel_identity(destination, row)
    return identity


def verify_staged(target, record):
    require(sha256(target / 'base.oci.tar') == record['archive_sha256'], 'STAGED_BASE_CHANGED')
    for name in PROFILES:
        folder = target / name
        for filename, key in (('roots.txt', 'roots_sha256'), ('requirements.lock', 'lock_sha256'),
                              ('inventory.json', 'inventory_sha256')):
            plain_path(folder / filename)
            require(sha256(folder / filename) == record['profiles'][name][key], 'STAGED_PROFILE_CHANGED')
        inventory = inventory_and_lock(folder)
        plain_path(folder / 'wheels')
        require({p.name for p in (folder / 'wheels').iterdir()} == {r['file'] for r in inventory},
                'STAGED_WHEEL_INVENTORY_CHANGED')
        for row in inventory:
            path = folder / 'wheels' / row['file']
            plain_path(path)
            require(sha256(path) == row['sha256'], 'STAGED_WHEEL_CHANGED')


def dockerfile(name, base):
    ports = '8000 9091' if name == 'transaction-api' else '8001 9090'
    return ('FROM ' + base + '\n\nWORKDIR /app\n'
            'COPY --from=python-wheels / /opt/wheels/\n'
            'COPY requirements-runtime.lock .\n'
            'RUN PIP_CONFIG_FILE=/dev/null python -I -m pip --isolated --disable-pip-version-check '
            '--no-cache-dir install --no-index --find-links=/opt/wheels --only-binary=:all: '
            '--require-hashes -r requirements-runtime.lock && python -I -m pip check && rm -r /opt/wheels\n\n'
            'COPY app ./app\nCOPY run.py .\nRUN useradd -r -u 10001 fdsuser\nUSER fdsuser\n'
            'EXPOSE ' + ports + '\nCMD ["python", "run.py"]\n')


def validate_source(root, config, staged):
    record = config['offline_supply']
    for path, digest in record['requirements'].items():
        require(sha256(root / path) == digest, 'SOURCE_REQUIREMENTS_CHANGED')
    for name in SERVICES:
        service = config['services'][name]
        require(service['context'] == name and service['dockerfile'] == name + '/Dockerfile',
                'OFFLINE_SOURCE_LAYOUT_UNSUPPORTED')
        runtime_roots = (root / name / 'requirements.txt').read_text().splitlines()
        test_roots = (root / name / 'requirements-test.txt').read_text().splitlines()
        require(test_roots.count('-r requirements.txt') == 1, 'TEST_REQUIREMENTS_PROFILE_UNSUPPORTED')
        for mode in ('runtime', 'test'):
            roots = runtime_roots + ([line for line in test_roots if line != '-r requirements.txt']
                                     if mode == 'test' else [])
            require((staged / (name + '-' + mode) / 'roots.txt').read_bytes()
                    == ('\n'.join(roots) + '\n').encode(), 'SOURCE_ROOTS_MISMATCH')
            require((root / name / ('requirements-' + mode + '.lock')).read_bytes()
                    == (staged / (name + '-' + mode) / 'requirements.lock').read_bytes(),
                    'SOURCE_LOCK_MISMATCH')
        # Narrow supported app profile: no remote ADD/COPY, frontend, stages or hidden pull.
        actual = (root / service['dockerfile']).read_text()
        require(not any(re.match(r'\s*#\s*(syntax|escape)\s*=', l, re.I) for l in actual.splitlines()),
                'DOCKERFILE_DIRECTIVE_UNSUPPORTED')
        lines = lambda value: [l.strip() for l in value.splitlines() if l.strip() and not l.lstrip().startswith('#')]
        require(lines(actual) == lines(dockerfile(name, service['base_image'])), 'OFFLINE_DOCKERFILE_PROFILE_REQUIRED')


def import_base(command, podman, record, staged, base):
    identity = inspect_archive(staged / 'base.oci.tar')
    require(identity['archive_sha256'] == record['archive_sha256'] and
            identity['manifest_digest'] == record['manifest_digest'] and
            identity['config_digest'] == record['config_digest'], 'STAGED_BASE_CHANGED')
    require(base.endswith('@' + record['manifest_digest']), 'OFFLINE_BASE_BINDING')
    command('offline-base-load', podman + ['load', '--input', str(staged / 'base.oci.tar')], timeout=600)
    # Name only the image in this NEW store; this tag is never the Dockerfile FROM.
    alias = base.split('@')[0] + ':cicd-offline-' + record['manifest_digest'][7:23]
    command('offline-base-tag', podman + ['tag', record['config_digest'], alias])
    output = command('offline-base-resolve', podman + ['image', 'inspect', '--format=json', base])
    require(output.stat().st_size <= 1024**2, 'IMAGE_INSPECT_TOO_LARGE')
    images = json.loads(output.read_bytes(), object_pairs_hook=unique_object)
    require(isinstance(images, list) and len(images) == 1, 'OFFLINE_IMAGE_COUNT')
    image = images[0]
    require(image.get('Id', '').removeprefix('sha256:') == record['config_digest'][7:]
            and image.get('Digest') == record['manifest_digest']
            and base in image.get('RepoDigests', [])
            and image.get('Os') == 'linux' and image.get('Architecture') == 'amd64'
            and image.get('RootFS', {}).get('Layers') == identity['diff_ids'], 'OFFLINE_DIGEST_RESOLUTION_MISMATCH')
    return {'REPOSITORY_DIGEST_RESOLUTION': 'PASS', 'reference': base, **identity}


def install_tests(command, tests_root, staged):
    for name in SERVICES:
        cwd = tests_root / name
        require(not (cwd / '.venv').exists(), 'OFFLINE_VENV_ALREADY_EXISTS')
        python = str(cwd / '.venv/bin/python')
        command(name + '-offline-venv', ['/usr/bin/python3', '-I', '-B', '-m', 'venv', str(cwd / '.venv')], cwd, 120)
        command(name + '-offline-install', [python, '-I', '-B', '-m', 'pip', '--isolated',
                '--disable-pip-version-check', '--no-cache-dir', 'install', '--no-index',
                '--find-links', str(staged / (name + '-test') / 'wheels'), '--only-binary=:all:',
                '--require-hashes', '-r', str(cwd / 'requirements-test.lock')], cwd, 600)
        command(name + '-offline-check', [python, '-I', '-B', '-m', 'pip', '--isolated',
                '--disable-pip-version-check', 'check'], cwd, 120)
