"""Shared synthetic OCI fixtures; these layers are not runnable applications."""
import hashlib
import io
import json
import tarfile

import oci_archive as oci


def encoded(value):
    return json.dumps(value).encode()


def image_members():
    members = {'oci-layout': encoded({'imageLayoutVersion': '1.0.0'})}

    def add(raw, media_type):
        digest = hashlib.sha256(raw).hexdigest()
        members['blobs/sha256/' + digest] = raw
        return {'mediaType': media_type, 'digest': 'sha256:' + digest, 'size': len(raw)}

    layer = add(b'fixture layer bytes; not a runnable application',
                'application/vnd.oci.image.layer.v1.tar')
    config = add(encoded({'os': 'linux', 'architecture': 'amd64',
                          'rootfs': {'type': 'layers', 'diff_ids': [layer['digest']]}}), oci.CONFIG)
    manifest = add(encoded({'schemaVersion': 2, 'mediaType': oci.MANIFEST,
                            'config': config, 'layers': [layer]}), oci.MANIFEST)
    members['index.json'] = encoded({'schemaVersion': 2, 'manifests': [manifest]})
    return members


def write_archive(path, members, extra=None):
    with tarfile.open(path, 'w') as tar:
        for name, raw in members.items():
            item = tarfile.TarInfo(name)
            item.size = len(raw)
            tar.addfile(item, io.BytesIO(raw))
        if extra:
            tar.addfile(extra)
