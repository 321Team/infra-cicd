"""Reviewed public READY helper; no credentials or selected host configuration."""
from pathlib import Path
import sys

if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from delivery_ready_pull import remote_main
    sys.exit(remote_main())
