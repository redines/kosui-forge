#!/usr/bin/env bash
set -euo pipefail

artifacts_dir="${CI_ARTIFACTS_DIR:-ci-artifacts}"
smoke_venv="${CI_SMOKE_VENV:-.venv-smoke}"
root_dir="$(pwd)"

rm -rf "$artifacts_dir" "$smoke_venv" dist build coverage.xml test-results
mkdir -p "$artifacts_dir" test-results

python -m pip install --upgrade pip
python -m pip install -e . pytest pytest-cov ruff mypy build

python -m pytest -q \
  --cov=src \
  --cov=tests \
  --cov-branch \
  --cov-report=term-missing:skip-covered \
  --cov-report=xml:coverage.xml \
  --junitxml=test-results/pytest.xml

python -m ruff check src tests
python -m ruff format --check src tests
python -m mypy src
python -m compileall -q src tests
python -m build

python -m venv "$smoke_venv"
# shellcheck disable=SC1090
. "$smoke_venv/bin/activate"
python -m pip install --upgrade pip
python -m pip install dist/*.whl
repo-bootstrap --version > "$root_dir/$artifacts_dir/repo-bootstrap-version.txt"
repo-bootstrap --help > "$root_dir/$artifacts_dir/repo-bootstrap-help.txt"
repo-bootstrap doctor --help > "$root_dir/$artifacts_dir/repo-bootstrap-doctor-help.txt"
deactivate
rm -rf "$smoke_venv"

git diff --check
sha256sum dist/* > "$artifacts_dir/SHA256SUMS.txt"
python - <<'PY' > "$artifacts_dir/build-metadata.txt"
from __future__ import annotations

import os
import platform
import subprocess
from pathlib import Path

root = Path.cwd()
dist = root / "dist"
files = sorted(path.name for path in dist.iterdir())
print(f"commit={subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()}")
print(f"version={subprocess.check_output(['python', '-c', 'import kosui_forge; print(kosui_forge.__version__)'], text=True).strip()}")
print(f"python={platform.python_version()}")
print(f"platform={platform.platform()}")
print(f"runner_os={os.environ.get('RUNNER_OS', 'local')}")
print(f"runner_arch={os.environ.get('RUNNER_ARCH', platform.machine())}")
print(f"artifacts={','.join(files)}")
PY
