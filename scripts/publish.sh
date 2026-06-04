#!/usr/bin/env bash
set -euo pipefail

ENVIRONMENT="${1:?Usage: publish.sh <dev|pre|prod>}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEFAULT_PARAMS="${ROOT}/infrastructure/parameters.${ENVIRONMENT}.json"
[[ -f "${DEFAULT_PARAMS}" ]] || DEFAULT_PARAMS="${ROOT}/infrastructure/parameters.json"
PARAMS="${PARAMS_FILE:-${DEFAULT_PARAMS}}"
VERSION="${VERSION:-$(date -u +%Y%m%d%H%M%S)}"
MANIFEST_DIR="${ROOT}/dist/manifests"

python3 "${ROOT}/scripts/validate_parameters.py" "${PARAMS}" "${ENVIRONMENT}" "${ROOT}/config/data-agent.yaml"

BUCKET="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["artifact_bucket_name"])' "${PARAMS}")"
ARTIFACT_KEY="artifacts/${ENVIRONMENT}/data-agent-${VERSION}.zip"
CONFIG_KEY="config/${ENVIRONMENT}/data-agent-${VERSION}.yaml"
MANIFEST_KEY="manifests/${ENVIRONMENT}/data-agent-${VERSION}.json"
ACTIVE_MANIFEST_KEY="manifests/${ENVIRONMENT}/active.json"
SHA256="$(shasum -a 256 "${ROOT}/dist/data-agent.zip" | awk '{print $1}')"

mkdir -p "${MANIFEST_DIR}"
python3 - "${MANIFEST_DIR}/data-agent-${VERSION}.json" "${VERSION}" "${ARTIFACT_KEY}" "${CONFIG_KEY}" "${SHA256}" <<'PY'
import json
import sys

path, version, artifact_key, config_key, sha256 = sys.argv[1:]
with open(path, "w", encoding="utf-8") as handle:
    json.dump(
        {
            "active_version": version,
            "artifact_key": artifact_key,
            "config_key": config_key,
            "artifact_sha256": sha256,
        },
        handle,
        indent=2,
    )
    handle.write("\n")
PY

aws s3 cp "${ROOT}/dist/data-agent.zip" "s3://${BUCKET}/${ARTIFACT_KEY}" \
  --only-show-errors
aws s3 cp "${ROOT}/config/data-agent.yaml" "s3://${BUCKET}/${CONFIG_KEY}" \
  --only-show-errors
aws s3 cp "${MANIFEST_DIR}/data-agent-${VERSION}.json" "s3://${BUCKET}/${MANIFEST_KEY}" \
  --only-show-errors
aws s3 cp "${MANIFEST_DIR}/data-agent-${VERSION}.json" "s3://${BUCKET}/${ACTIVE_MANIFEST_KEY}" \
  --only-show-errors

echo "ARTIFACT_KEY=${ARTIFACT_KEY}"
echo "CONFIG_KEY=${CONFIG_KEY}"
echo "MANIFEST_KEY=${MANIFEST_KEY}"
