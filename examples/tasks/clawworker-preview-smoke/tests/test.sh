#!/bin/sh
set -eu
/opt/hermes/.venv/bin/python - <<'PY'
import json
from pathlib import Path

path = Path('/workspace/work/preview-proof.json')
expected = {'native': True, 'workspace': '/workspace/work', 'adp_key_present': True}
passed = path.is_file() and json.loads(path.read_text()) == expected
Path('/logs/verifier/reward.txt').write_text('1\n' if passed else '0\n')
PY
