"""Fixed remote entrypoint for `python -I /reviewed/path/this-file.py UID SEC`.

The immutable reviewed helper directory is the only import addition. No cwd,
PYTHONPATH, request, SSH environment or input configuration selects Python code.
Deployment path/code ownership is an explicit SITE adoption precondition.
"""
from pathlib import Path
import sys

if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from delivery_ssh_bridge import remote_main
    sys.exit(remote_main())
