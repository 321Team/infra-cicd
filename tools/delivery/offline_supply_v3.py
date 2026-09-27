"""Fail-closed offline supply for DEC-P1-IMG-01 Wolfi/apko two-stage builds.

Schema v3 keeps builder/runtime apko Docker-loadable archives distinct and never
downloads or rewrites application inputs. Archive/config/layer identities bind
each loaded local image. None of these identities proves Harbor Push
or Kubernetes deployment.
"""
import gzip
import hashlib
import json
from pathlib import Path
import re
import tarfile

import offline_supply as legacy

SERVICES = legacy.SERVICES
PROFILES = legacy.PROFILES
ROLES = ('builder', 'runtime')
SOURCE_INPUTS = (
    'ci/apko/wolfi-python313-builder.apko.yaml',
    'ci/apko/wolfi-python313-builder.apko.lock.json',
    'ci/apko/wolfi-python313-runtime.apko.yaml',
    'ci/apko/wolfi-python313-runtime.apko.lock.json',
)
HEX = legacy.HEX
LIMIT = legacy.LIMIT
require = legacy.require
sha256 = legacy.sha256
unique_object = legacy.unique_object
IDENTITY_FIELDS = (
    'format', 'archive_sha256', 'manifest_json_sha256', 'index_json_sha256',
    'config_digest', 'diff_ids', 'layer_count', 'repo_tag', 'platform',
)


def _hash_stream(stream):
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
        digest.update(chunk)
    return digest.hexdigest()


def inspect_supply_archive(path):
    """Validate the Docker save/load archive emitted by pinned apko v1.4.4.

    The archive is not an OCI image-layout tar. It contains manifest.json,
    index.json, a config object named by digest, and compressed layer members.
    """
    legacy.plain_path(path)
    before = sha256(path)
    with tarfile.open(path, 'r:*') as archive:
        entries = {}
        total = 0
        for item in archive:
            name = item.name.rstrip('/') if item.isdir() else item.name
            require(len(entries) < 4096 and name not in entries,
                    'DOCKER_ARCHIVE_DUPLICATE_OR_TOO_MANY_MEMBERS')
            require(item.isfile(), 'DOCKER_ARCHIVE_LINK_DIRECTORY_OR_SPECIAL_FILE')
            require(name in ('manifest.json', 'index.json')
                    or re.fullmatch(r'sha256:[0-9a-f]{64}', name)
                    or re.fullmatch(r'[0-9a-f]{64}\.tar(?:\.gz)?', name),
                    'DOCKER_ARCHIVE_MEMBER_INVALID')
            total += item.size
            require(total <= LIMIT, 'DOCKER_ARCHIVE_TOO_LARGE')
            entries[name] = item

        def raw(name, maximum=4 * 1024 * 1024):
            member = entries.get(name)
            require(member is not None and member.size <= maximum,
                    'DOCKER_ARCHIVE_MEMBER_MISSING_OR_TOO_LARGE')
            with archive.extractfile(member) as stream:
                require(stream is not None, 'DOCKER_ARCHIVE_MEMBER_READ_FAILED')
                return stream.read()

        manifest_raw = raw('manifest.json')
        manifest = json.loads(manifest_raw, object_pairs_hook=unique_object)
        require(isinstance(manifest, list) and len(manifest) == 1
                and isinstance(manifest[0], dict), 'DOCKER_ARCHIVE_SINGLE_IMAGE_REQUIRED')
        row = manifest[0]
        require(set(row) == {'Config', 'RepoTags', 'Layers'}, 'DOCKER_ARCHIVE_MANIFEST_FIELDS')
        config_name, repo_tags, layers = row['Config'], row['RepoTags'], row['Layers']
        require(isinstance(config_name, str) and config_name in entries,
                'DOCKER_ARCHIVE_CONFIG_MISSING')
        require(isinstance(repo_tags, list) and len(repo_tags) == 1
                and isinstance(repo_tags[0], str) and repo_tags[0]
                and len(repo_tags[0]) <= 512 and not any(ord(ch) < 32 for ch in repo_tags[0]),
                'DOCKER_ARCHIVE_REPOTAG_INVALID')
        require(isinstance(layers, list) and 0 < len(layers) <= 128
                and all(isinstance(name, str)
                        and re.fullmatch(r'[0-9a-f]{64}\.tar(?:\.gz)?', name)
                        and name in entries for name in layers),
                'DOCKER_ARCHIVE_LAYERS_INVALID')

        config_raw = raw(config_name)
        config_digest = 'sha256:' + hashlib.sha256(config_raw).hexdigest()
        require(config_name in (config_digest, config_digest[7:] + '.json'),
                'DOCKER_ARCHIVE_CONFIG_DIGEST_MISMATCH')
        config = json.loads(config_raw, object_pairs_hook=unique_object)
        require(isinstance(config, dict) and config.get('os') == 'linux'
                and config.get('architecture') == 'amd64',
                'DOCKER_ARCHIVE_PLATFORM_UNSUPPORTED')
        rootfs = config.get('rootfs', {})
        diff_ids = rootfs.get('diff_ids') if isinstance(rootfs, dict) else None
        require(rootfs.get('type') == 'layers' and isinstance(diff_ids, list)
                and len(diff_ids) == len(layers)
                and all(isinstance(value, str)
                        and re.fullmatch(r'sha256:[0-9a-f]{64}', value) for value in diff_ids),
                'DOCKER_ARCHIVE_DIFF_IDS_INVALID')

        for name, diff_id in zip(layers, diff_ids):
            member = entries[name]
            require(member.size <= LIMIT, 'DOCKER_ARCHIVE_LAYER_TOO_LARGE')
            with archive.extractfile(member) as stream:
                require(stream is not None, 'DOCKER_ARCHIVE_LAYER_READ_FAILED')
                if name.endswith('.gz'):
                    with gzip.GzipFile(fileobj=stream, mode='rb') as unpacked:
                        actual = _hash_stream(unpacked)
                else:
                    actual = _hash_stream(stream)
            require('sha256:' + actual == diff_id, 'DOCKER_ARCHIVE_DIFF_ID_MISMATCH')

        index_raw = raw('index.json')
        index = json.loads(index_raw, object_pairs_hook=unique_object)
        require(isinstance(index, dict), 'DOCKER_ARCHIVE_INDEX_INVALID')

    require(sha256(path) == before, 'DOCKER_ARCHIVE_CHANGED')
    return {
        'format': 'docker-archive',
        'archive_sha256': before,
        'manifest_json_sha256': hashlib.sha256(manifest_raw).hexdigest(),
        'index_json_sha256': hashlib.sha256(index_raw).hexdigest(),
        'config_digest': config_digest,
        'diff_ids': diff_ids,
        'layer_count': len(layers),
        'repo_tag': repo_tags[0],
        'platform': 'linux/amd64',
    }


def _validate_identity(image):
    require(isinstance(image, dict) and set(image) == set(IDENTITY_FIELDS),
            'OFFLINE_V3_IMAGE_FIELDS')
    require(image.get('format') == 'docker-archive'
            and isinstance(image.get('archive_sha256'), str)
            and re.fullmatch(HEX, image['archive_sha256'])
            and isinstance(image.get('manifest_json_sha256'), str)
            and re.fullmatch(HEX, image['manifest_json_sha256'])
            and isinstance(image.get('index_json_sha256'), str)
            and re.fullmatch(HEX, image['index_json_sha256'])
            and isinstance(image.get('config_digest'), str)
            and re.fullmatch('sha256:' + HEX, image['config_digest'])
            and isinstance(image.get('diff_ids'), list)
            and 0 < len(image['diff_ids']) <= 128
            and all(isinstance(value, str)
                    and re.fullmatch('sha256:' + HEX, value) for value in image['diff_ids'])
            and type(image.get('layer_count')) is int
            and image['layer_count'] == len(image['diff_ids'])
            and isinstance(image.get('repo_tag'), str) and image['repo_tag']
            and len(image['repo_tag']) <= 512
            and not any(ord(ch) < 32 for ch in image['repo_tag'])
            and image.get('platform') == 'linux/amd64',
            'OFFLINE_IDENTITY_REQUIRED')


def validate_record(record):
    require(isinstance(record, dict) and set(record) == {
        'directory', 'images', 'profiles', 'requirements', 'source_inputs'},
            'OFFLINE_V3_RECORD_FIELDS')
    directory = record['directory']
    require(isinstance(directory, str) and re.fullmatch(r'/[A-Za-z0-9_./-]+', directory)
            and all(p not in ('', '.', '..') for p in directory.split('/')[1:]),
            'OFFLINE_DIRECTORY_INVALID')
    images = record['images']
    require(isinstance(images, dict) and set(images) == set(ROLES), 'OFFLINE_V3_EXACT_TWO_IMAGES')
    for image in images.values():
        _validate_identity(image)
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
    require(isinstance(record['source_inputs'], dict)
            and set(record['source_inputs']) == set(SOURCE_INPUTS)
            and all(isinstance(v, str) and re.fullmatch(HEX, v)
                    for v in record['source_inputs'].values()),
            'OFFLINE_SOURCE_INPUT_HASH_REQUIRED')
    return record


def _identity_matches(actual, expected):
    return actual == expected


def stage(record, target):
    validate_record(record)
    source = Path(record['directory'])
    legacy.plain_path(source)
    target.mkdir(mode=0o700)
    total = 0
    identities = {}
    for role in ROLES:
        expected = record['images'][role]
        filename = role + '.oci.tar'
        total += legacy.copy_checked(source / filename, target / filename,
                                     expected['archive_sha256'], LIMIT)
        require(total <= LIMIT, 'SUPPLY_TOTAL_SIZE_LIMIT')
        identity = inspect_supply_archive(target / filename)
        require(_identity_matches(identity, expected), 'SUPPLY_IMAGE_IDENTITY_MISMATCH:' + role)
        identities[role] = identity
    for name in PROFILES:
        folder = target / name
        folder.mkdir(mode=0o700)
        for filename, key in (('roots.txt', 'roots_sha256'), ('requirements.lock', 'lock_sha256'),
                              ('inventory.json', 'inventory_sha256')):
            legacy.copy_checked(source / name / filename, folder / filename,
                                record['profiles'][name][key], 1024**2)
        inventory = legacy.inventory_and_lock(folder)
        wheels = source / name / 'wheels'
        legacy.plain_path(wheels)
        require({p.name for p in wheels.iterdir()} == {r['file'] for r in inventory},
                'WHEELHOUSE_INVENTORY_MISMATCH')
        (folder / 'wheels').mkdir(mode=0o700)
        for row in inventory:
            destination = folder / 'wheels' / row['file']
            total += legacy.copy_checked(wheels / row['file'], destination, row['sha256'], 64 * 1024**2)
            require(total <= LIMIT, 'SUPPLY_TOTAL_SIZE_LIMIT')
            legacy.wheel_identity(destination, row)
    return identities


def verify_staged(target, record):
    for role in ROLES:
        expected = record['images'][role]
        path = target / (role + '.oci.tar')
        legacy.plain_path(path)
        require(sha256(path) == expected['archive_sha256'], 'STAGED_IMAGE_CHANGED:' + role)
        require(_identity_matches(inspect_supply_archive(path), expected),
                'STAGED_IMAGE_IDENTITY_CHANGED:' + role)
    for name in PROFILES:
        folder = target / name
        for filename, key in (('roots.txt', 'roots_sha256'), ('requirements.lock', 'lock_sha256'),
                              ('inventory.json', 'inventory_sha256')):
            legacy.plain_path(folder / filename)
            require(sha256(folder / filename) == record['profiles'][name][key],
                    'STAGED_PROFILE_CHANGED')
        inventory = legacy.inventory_and_lock(folder)
        legacy.plain_path(folder / 'wheels')
        require({p.name for p in (folder / 'wheels').iterdir()} == {r['file'] for r in inventory},
                'STAGED_WHEEL_INVENTORY_CHANGED')
        for row in inventory:
            path = folder / 'wheels' / row['file']
            legacy.plain_path(path)
            require(sha256(path) == row['sha256'], 'STAGED_WHEEL_CHANGED')


def dockerfile(name):
    require(name in SERVICES, 'SERVICE_UNSUPPORTED')
    ports = '8000 9091' if name == 'transaction-api' else '8001 9090'
    return ('# BUILDER_IMAGE and RUNTIME_IMAGE must come from the repo-pinned apko configs/lockfiles.\n'
            'ARG BUILDER_IMAGE\nARG RUNTIME_IMAGE\n\n'
            'FROM ' + '$' + '{BUILDER_IMAGE} AS build\nWORKDIR /app\n'
            'RUN python3.13 -m venv --copies /venv\n'
            'COPY --from=python-wheels / /opt/wheels/\n'
            'COPY requirements-runtime.lock .\n'
            'RUN PIP_CONFIG_FILE=/dev/null /venv/bin/python -I -m pip --isolated '
            '--disable-pip-version-check --no-cache-dir install --no-index --find-links=/opt/wheels '
            '--only-binary=:all: --require-hashes -r requirements-runtime.lock \\\n'
            '    && PIP_CONFIG_FILE=/dev/null /venv/bin/python -I -m pip --isolated '
            '--disable-pip-version-check check \\\n'
            '    && PIP_CONFIG_FILE=/dev/null /venv/bin/python -I -m pip --isolated '
            '--disable-pip-version-check uninstall -y pip setuptools \\\n'
            '    && rm -rf /opt/wheels\n\n'
            'FROM ' + '$' + '{RUNTIME_IMAGE}\n'
            'ENV PYTHONDONTWRITEBYTECODE=1 \\\n    PYTHONUNBUFFERED=1\n'
            'WORKDIR /app\nCOPY --from=build /venv /venv\n'
            'COPY app ./app\nCOPY run.py .\nUSER 10001:999\n'
            'EXPOSE ' + ports + '\nENTRYPOINT ["/venv/bin/python3"]\nCMD ["run.py"]\n')


def validate_source(root, config, staged):
    record = config['offline_supply']
    for path, digest in record['source_inputs'].items():
        require(sha256(root / path) == digest, 'SOURCE_APKO_INPUT_CHANGED:' + path)
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
        require((root / service['dockerfile']).read_bytes() == dockerfile(name).encode(),
                'OFFLINE_DOCKERFILE_PROFILE_REQUIRED')


def _local_reference(role, identity):
    return 'localhost/fds-apko-' + role + ':cicd-offline-' + identity['config_digest'][7:23]


def import_images(command, podman, record, staged):
    imported = {}
    for role in ROLES:
        expected = record['images'][role]
        archive = staged / (role + '.oci.tar')
        identity = inspect_supply_archive(archive)
        require(_identity_matches(identity, expected), 'STAGED_IMAGE_CHANGED:' + role)
        command('offline-' + role + '-load',
                podman + ['load', '--input', str(archive)], timeout=600)
        reference = _local_reference(role, identity)
        command('offline-' + role + '-tag',
                podman + ['tag', expected['config_digest'], reference])
        output = command('offline-' + role + '-resolve',
                         podman + ['image', 'inspect', '--format=json', reference])
        require(output.stat().st_size <= 1024**2, 'IMAGE_INSPECT_TOO_LARGE')
        images = json.loads(output.read_bytes(), object_pairs_hook=unique_object)
        require(isinstance(images, list) and len(images) == 1, 'OFFLINE_IMAGE_COUNT')
        image = images[0]
        require(image.get('Id', '').removeprefix('sha256:') == expected['config_digest'][7:]
                and image.get('Os') == 'linux' and image.get('Architecture') == 'amd64'
                and image.get('RootFS', {}).get('Layers') == identity['diff_ids'],
                'OFFLINE_IMAGE_LOAD_IDENTITY_MISMATCH:' + role)
        imported[role] = {'reference': reference, **identity}
    return imported


def _container_prefix(podman, tests_root, staged, builder, cwd):
    legacy.plain_path(tests_root)
    legacy.plain_path(staged)
    require(isinstance(cwd, str) and cwd and '\\' not in cwd
            and not cwd.startswith('/') and all(p not in ('', '.', '..') for p in cwd.split('/')),
            'TEST_CWD_INVALID')
    return podman + [
        'run', '--rm', '--pull=never', '--network=none', '--security-opt=label=disable',
        '--env', 'HOME=/tmp', '--env', 'PIP_CONFIG_FILE=/dev/null',
        '--volume', str(tests_root) + ':/src:rw',
        '--volume', str(staged) + ':/supply:ro',
        '--workdir', '/src/' + cwd, builder]


def install_tests(command, podman, tests_root, staged, builder):
    for name in SERVICES:
        cwd = tests_root / name
        require(not (cwd / '.venv').exists(), 'OFFLINE_VENV_ALREADY_EXISTS')
        prefix = _container_prefix(podman, tests_root, staged, builder, name)
        command(name + '-offline-venv',
                prefix + ['python3.13', '-I', '-B', '-m', 'venv', '--copies', '.venv'],
                timeout=120)
        command(name + '-offline-install',
                prefix + ['.venv/bin/python', '-I', '-B', '-m', 'pip', '--isolated',
                          '--disable-pip-version-check', '--no-cache-dir', 'install', '--no-index',
                          '--find-links', '/supply/' + name + '-test/wheels',
                          '--only-binary=:all:', '--require-hashes',
                          '-r', 'requirements-test.lock'], timeout=600)
        command(name + '-offline-check',
                prefix + ['.venv/bin/python', '-I', '-B', '-m', 'pip', '--isolated',
                          '--disable-pip-version-check', 'check'], timeout=120)


def test_argv(podman, tests_root, staged, builder, step):
    return _container_prefix(podman, tests_root, staged, builder, step['cwd']) + step['argv']
