#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="${ROOT}/build/package"
DIST_DIR="${ROOT}/dist"
ARTIFACT="${DIST_DIR}/data-agent.zip"

rm -rf "${ROOT}/build" "${DIST_DIR}"
mkdir -p "${BUILD_DIR}" "${DIST_DIR}"

python3 -m pip install \
  --requirement "${ROOT}/requirements.txt" \
  --target "${BUILD_DIR}" \
  --platform manylinux2014_aarch64 \
  --implementation cp \
  --python-version 3.13 \
  --only-binary=:all:

cp -R "${ROOT}/app" "${BUILD_DIR}/app"
cp "${ROOT}/main.py" "${BUILD_DIR}/main.py"

find "${BUILD_DIR}" -type d -name '__pycache__' -prune -exec rm -rf {} +
find "${BUILD_DIR}" -type f -name '*.pyc' -delete

(
  cd "${BUILD_DIR}"
  zip -qr "${ARTIFACT}" .
)

shasum -a 256 "${ARTIFACT}" > "${ARTIFACT}.sha256"
echo "Built ${ARTIFACT}"
