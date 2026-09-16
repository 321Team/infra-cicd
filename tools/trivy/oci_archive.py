"""Read a single linux/amd64 OCI image without extracting archive members."""
import hashlib
import json
import re
import tarfile

from prepare_db import require, regular, unique_object
from verify_runner import sha256

MANIFEST = 'application/vnd.oci.image.manifest.v1+json'
CONFIG = 'application/vnd.oci.image.config.v1+json'
LAYERS = {
    'application/vnd.oci.image.layer.v1.tar',
    'application/vnd.oci.image.layer.v1.tar+gzip',
    'application/vnd.oci.image.layer.v1.tar+zstd',
}
DIGEST = r'sha256:[0-9a-f]{64}'


def json_object(raw):
    value = json.loads(raw, object_pairs_hook=unique_object)
    require(isinstance(value, dict), 'JSON_OBJECT_REQUIRED')
    return value


def inspect_archive(path):
    """This restricted profile refuses indexes, external blobs and mixed formats.

    archive_sha256, manifest_digest and config_digest are different identities.
    None is an observation of a registry Push or deployed image.
    """
    regular(path)
    before = sha256(path)
    with tarfile.open(path, 'r:') as archive:
        entries = {}
        for item in archive:
            name = item.name.rstrip('/') if item.isdir() else item.name
            require(len(entries) < 4096 and name not in entries, 'OCI_DUPLICATE_OR_TOO_MANY_MEMBERS')
            if item.isdir():
                require(name in ('blobs', 'blobs/sha256'), 'OCI_DIRECTORY_INVALID')
            else:
                require(item.isfile(), 'OCI_LINK_OR_SPECIAL_FILE')
                require(name in ('index.json', 'oci-layout') or
                        re.fullmatch(r'blobs/sha256/[0-9a-f]{64}', name), 'OCI_MEMBER_INVALID')
            entries[name] = item

        def read_json(name):
            member = entries.get(name)
            require(member is not None and member.isfile() and member.size <= 4 * 1024 * 1024,
                    'OCI_JSON_MISSING_OR_TOO_LARGE')
            with archive.extractfile(member) as stream:
                return json_object(stream.read())

        def blob(descriptor, media_types):
            require(isinstance(descriptor, dict), 'OCI_DESCRIPTOR_INVALID')
            require(descriptor.get('mediaType') in media_types, 'OCI_MEDIA_TYPE_UNSUPPORTED')
            digest = descriptor.get('digest')
            require(isinstance(digest, str) and re.fullmatch(DIGEST, digest), 'OCI_DIGEST_INVALID')
            require('urls' not in descriptor and 'data' not in descriptor, 'OCI_EXTERNAL_OR_INLINE_BLOB')
            name = 'blobs/sha256/' + digest.removeprefix('sha256:')
            member = entries.get(name)
            require(member is not None and member.isfile(), 'OCI_BLOB_MISSING')
            require(type(descriptor.get('size')) is int and descriptor['size'] == member.size,
                    'OCI_BLOB_SIZE_MISMATCH')
            with archive.extractfile(member) as stream:
                actual = hashlib.file_digest(stream, 'sha256').hexdigest()
            require('sha256:' + actual == digest, 'OCI_BLOB_HASH_MISMATCH')
            return name

        require(read_json('oci-layout').get('imageLayoutVersion') == '1.0.0', 'OCI_LAYOUT_VERSION')
        index = read_json('index.json')
        require(type(index.get('schemaVersion')) is int and index['schemaVersion'] == 2,
                'OCI_INDEX_SCHEMA')
        manifests = index.get('manifests')
        require(isinstance(manifests, list) and len(manifests) == 1, 'OCI_SINGLE_IMAGE_REQUIRED')
        manifest = read_json(blob(manifests[0], {MANIFEST}))
        require(type(manifest.get('schemaVersion')) is int and manifest['schemaVersion'] == 2
                and manifest.get('mediaType') == MANIFEST, 'OCI_MANIFEST_SCHEMA')
        config = read_json(blob(manifest.get('config'), {CONFIG}))
        require(config.get('os') == 'linux' and config.get('architecture') == 'amd64',
                'OCI_PLATFORM_UNSUPPORTED')
        layers = manifest.get('layers')
        require(isinstance(layers, list) and 0 < len(layers) <= 512, 'OCI_LAYERS_REQUIRED')
        for layer in layers:
            blob(layer, LAYERS)
        rootfs = config.get('rootfs', {})
        require(isinstance(rootfs, dict) and rootfs.get('type') == 'layers', 'OCI_ROOTFS_INVALID')
        diff_ids = rootfs.get('diff_ids')
        require(isinstance(diff_ids, list) and len(diff_ids) == len(layers) and
                all(isinstance(d, str) and re.fullmatch(DIGEST, d) for d in diff_ids),
                'OCI_DIFF_IDS_INVALID')
    require(sha256(path) == before, 'OCI_ARCHIVE_CHANGED')
    return {'archive_sha256': before, 'manifest_digest': manifests[0]['digest'],
            'config_digest': manifest['config']['digest'], 'diff_ids': diff_ids,
            'platform': 'linux/amd64'}
