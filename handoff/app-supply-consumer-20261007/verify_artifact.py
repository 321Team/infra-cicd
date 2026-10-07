"""Read-only local artifact inspection. No install, image load, or runtime action.

Usage: python verify_artifact.py ARTIFACT.zip REPORT.json
Requires Python 3.11+ and authenticated gh read access to the exact public source.
Any failed assertion stops verification; keep the original archive for diagnosis.
"""
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.parser import BytesParser
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tarfile
import zipfile

SOURCE = 'adc90410f99cb7af5b40bc8ff92c45272e9f8bee'
DIGEST = '2eaed6486b68f9260f735e362b45a167d7f01aa0fa819accf751ea8d74951ad6'
LIMIT = 1024**3

def sha(data):
    return hashlib.sha256(data).hexdigest()

def unique(pairs):
    result = {}
    for key, value in pairs:
        assert key not in result, 'Duplicate JSON field'
        result[key] = value
    return result

def obj(raw):
    return json.loads(raw, object_pairs_hook=unique)

def normalize(name):
    return re.sub(r'[-_.]+', '-', name).lower()

def source_bytes(path):
    response = obj(subprocess.check_output([
        'gh', 'api', f'repos/321Team/fds-msa/contents/{path}?ref={SOURCE}']))
    assert response['encoding'] == 'base64'
    return path, base64.b64decode(response['content'])

def image_identity(raw):
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:*') as archive:
        entries = archive.getmembers()
        assert 0 < len(entries) < 4096
        assert all(m.isfile() and m.size <= LIMIT for m in entries)
        assert len({m.name for m in entries}) == len(entries)
        assert sum(m.size for m in entries) <= LIMIT
        members = {m.name: m for m in entries}
        def read(name):
            assert members[name].size < 4*1024**2
            return archive.extractfile(members[name]).read()
        manifest_raw, index_raw = read('manifest.json'), read('index.json')
        manifest, index = obj(manifest_raw), obj(index_raw)
        assert isinstance(index, dict) and len(manifest) == 1
        row = manifest[0]
        assert set(row) == {'Config', 'RepoTags', 'Layers'}
        config_raw = read(row['Config'])
        config = obj(config_raw)
        config_digest = 'sha256:' + sha(config_raw)
        assert row['Config'] == config_digest
        assert (config['os'], config['architecture']) == ('linux', 'amd64')
        assert len(row['RepoTags']) == 1
        assert config['rootfs']['type'] == 'layers'
        expected = config['rootfs']['diff_ids']
        assert 0 < len(expected) == len(row['Layers']) <= 128
        assert index['schemaVersion'] == 2 and len(index['manifests']) == 1
        descriptor = index['manifests'][0]
        assert descriptor['platform'] == {'architecture':'amd64','os':'linux'}
        linked_raw = read(descriptor['digest'])
        assert len(linked_raw) == descriptor['size']
        assert 'sha256:' + sha(linked_raw) == descriptor['digest']
        linked = obj(linked_raw)
        assert linked['config']['digest'] == config_digest
        assert linked['config']['size'] == len(config_raw)
        assert len(linked['layers']) == len(row['Layers'])
        assert set(members) == {'manifest.json', 'index.json', row['Config'], descriptor['digest'], *row['Layers']}
        for name, layer in zip(row['Layers'], linked['layers']):
            assert members[name].size == layer['size']
            assert 'sha256:' + hashlib.file_digest(archive.extractfile(members[name]), 'sha256').hexdigest() == layer['digest']
        for name, digest in zip(row['Layers'], expected):
            assert re.fullmatch(r'[a-f0-9]{64}\.tar(?:\.gz)?', name)
            stream = archive.extractfile(members[name])
            if name.endswith('.gz'):
                stream = gzip.GzipFile(fileobj=stream)
            actual, count = hashlib.sha256(), 0
            while chunk := stream.read(1024**2):
                count += len(chunk)
                assert count <= LIMIT
                actual.update(chunk)
            assert 'sha256:' + actual.hexdigest() == digest, 'Layer diff_id mismatch'
        return dict(format='docker-archive', archive_sha256=sha(raw),
                    manifest_json_sha256=sha(manifest_raw), index_json_sha256=sha(index_raw),
                    config_digest=config_digest, diff_ids=expected, layer_count=len(expected),
                    repo_tag=row['RepoTags'][0], platform='linux/amd64')

def main():
    archive_path, report_path = map(Path, sys.argv[1:])
    with archive_path.open('rb') as stream:
        assert hashlib.file_digest(stream, 'sha256').hexdigest() == DIGEST
    assert archive_path.stat().st_size == 109786891
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        assert len(names) == len(set(names))
        assert all(not PurePosixPath(n).is_absolute() and '..' not in PurePosixPath(n).parts
                   and '\\' not in n for n in names)
        assert all(not i.is_dir() and (i.external_attr >> 16) & 0o170000 != 0o120000
                   for i in archive.infolist())
        assert sum(i.file_size for i in archive.infolist()) <= LIMIT
        assert archive.testzip() is None
        checksums = {}
        for line in archive.read('SHA256SUMS').decode().splitlines():
            digest, name = line.split('  ', 1)
            assert re.fullmatch('[a-f0-9]{64}', digest) and name not in checksums
            checksums[name] = digest
            with archive.open(name) as stream:
                assert hashlib.file_digest(stream, 'sha256').hexdigest() == digest, name
        assert set(checksums) == set(names) - {'SHA256SUMS'}
        manifest_raw = archive.read('SUPPLY_MANIFEST.json')
        manifest = obj(manifest_raw)
        assert manifest['schema'] == 'fds.apko-offline-supply.v1'
        assert manifest['source'] == manifest['supply_tool'] == {'repository':'321Team/fds-msa','commit':SOURCE}
        assert manifest['platform'] == 'linux/amd64'
        paths = list(manifest['requirements']) + ['ci/apko/' + n for n in manifest['apko_inputs']]
        with ThreadPoolExecutor(max_workers=4) as pool:
            source = dict(pool.map(source_bytes, paths))
        for path, digest in manifest['requirements'].items():
            assert sha(source[path]) == digest, path
        for name, digest in manifest['apko_inputs'].items():
            assert sha(archive.read('inputs/' + name)) == digest
            assert source['ci/apko/' + name] == archive.read('inputs/' + name)
        images = {role: image_identity(archive.read(role+'.oci.tar')) for role in ('builder','runtime')}
        assert images == manifest['images']
        counts = {}
        for profile, hashes in manifest['profiles'].items():
            prefix = 'profiles/' + profile + '/'
            for key, name in [('roots_sha256','roots.txt'),('lock_sha256','requirements.lock'),('inventory_sha256','inventory.json')]:
                assert sha(archive.read(prefix+name)) == hashes[key]
            service, mode = profile.rsplit('-', 1)
            assert archive.read(prefix+'requirements.lock') == source[service+'/requirements-'+mode+'.lock']
            roots = source[service+'/requirements.txt'].decode().splitlines()
            if mode == 'test':
                test_roots = source[service+'/requirements-test.txt'].decode().splitlines()
                assert test_roots.count('-r requirements.txt') == 1
                roots += [r for r in test_roots if r and r != '-r requirements.txt']
            assert archive.read(prefix+'roots.txt') == ('\n'.join(r.strip() for r in roots if r.strip())+'\n').encode()
            inventory = obj(archive.read(prefix+'inventory.json'))
            lock = {}
            for line in archive.read(prefix+'requirements.lock').decode().splitlines():
                if not line.strip(): continue
                match = re.fullmatch(r'([A-Za-z0-9_.-]+)(?:\[[^\]]+\])?==([^ ]+) --hash=sha256:([a-f0-9]{64})', line.strip())
                assert match, line
                key = normalize(match[1])
                assert key not in lock
                lock[key] = (match[2], match[3])
            assert len(inventory) == len(lock)
            assert {prefix+'wheels/'+r['file'] for r in inventory} == {n for n in names if n.startswith(prefix+'wheels/')}
            for row in inventory:
                assert (row['version'], row['sha256']) == lock[row['name']]
                wheel = archive.read(prefix+'wheels/'+row['file'])
                assert sha(wheel) == row['sha256']
                with zipfile.ZipFile(io.BytesIO(wheel)) as wz:
                    metas = [n for n in wz.namelist() if re.fullmatch(r'[^/]+\.dist-info/METADATA',n)]
                    assert len(metas) == 1 and wz.getinfo(metas[0]).file_size < 4*1024**2
                    metadata = BytesParser().parsebytes(wz.read(metas[0]))
                    assert len(metadata.get_all('Name',[])) == len(metadata.get_all('Version',[])) == 1
                    assert (normalize(metadata['Name']),metadata['Version']) == (row['name'],row['version'])
            counts[profile] = len(inventory)
        report = dict(checked_at=datetime.now(timezone.utc).isoformat(), source=SOURCE,
                      artifact_id=11466476253, archive_sha256=DIGEST, size_bytes=archive_path.stat().st_size,
                      zip_entries=len(names), manifest_files_matched=len(checksums), mismatch_count=0,
                      supply_manifest_sha256=sha(manifest_raw), source_files_matched=len(source),
                      images=images, profiles=manifest['profiles'], wheel_counts=counts,
                      wheel_total=sum(counts.values()), checker_sha256=sha(Path(__file__).read_bytes()),
                      scope='LOCAL_BYTES_SOURCE_HASH_PROFILE_WHEEL_METADATA_IMAGE_CONFIG_AND_LAYER_DIFF_IDS',
                      limitations=['NO_PACKAGE_INSTALL_OR_DEPENDENCY_RESOLUTION','NO_IMAGE_LOAD_OR_RUNTIME',
                                   'NO_VULNERABILITY_SCAN','NO_INDEPENDENT_APPROVAL','NO_DELIVERY_AUTHORIZATION'])
    report_path.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({k:report[k] for k in ('checked_at','manifest_files_matched','source_files_matched','wheel_counts','wheel_total','supply_manifest_sha256')}, indent=2))

if __name__ == '__main__':
    main()
