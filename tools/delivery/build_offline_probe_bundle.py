#!/usr/bin/env python3
"""Build an exclusive, deterministic zipapp; all input hashes come from config."""
import hashlib
import json
from pathlib import Path
import sys
import zipfile


def build(path):
    root = Path(__file__).resolve().parents[2]
    record = json.loads((root / 'config/app-build-scan.json').read_bytes())['offline_supply']
    files = {'__main__.py': b'from probe_offline_supply import main\nraise SystemExit(main())\n',
             'offline-probe-input.json': (json.dumps(record, sort_keys=True, indent=2) + '\n').encode()}
    for folder, modules in [('delivery', ('offline_supply', 'probe_offline_supply', 'build_scan')),
                            ('trivy', ('oci_archive', 'prepare_db', 'scan_images', 'verify_runner'))]:
        files.update({name + '.py': (root / 'tools' / folder / (name + '.py')).read_bytes() for name in modules})
    with path.open('xb') as stream, zipfile.ZipFile(stream, 'w') as archive:
        for name, raw in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(2026, 9, 17, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100600 << 16
            archive.writestr(info, raw)
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == '__main__':
    path = Path(sys.argv[1])
    print(build(path) + '  ' + path.name)
