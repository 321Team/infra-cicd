#!/usr/bin/env python3
"""Create an exclusive deterministic zipapp from the reviewed supply sources."""
from pathlib import Path
import hashlib
import sys
import zipfile

MODULES = ('collect_runner_supply', 'supply_inputs', 'supply_transport',
           'supply_wheels', 'supply_pip_bridge')


def build(path):
    root = Path(__file__).parent
    with path.open('xb') as out, zipfile.ZipFile(out, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        files = {'__main__.py': b'from collect_runner_supply import main\nraise SystemExit(main())\n'}
        files.update({name + '.py': (root / (name + '.py')).read_bytes() for name in MODULES})
        for name, raw in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(2026, 9, 17, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100600 << 16
            archive.writestr(info, raw)
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == '__main__':
    destination = Path(sys.argv[1])
    print(build(destination) + '  ' + destination.name)
