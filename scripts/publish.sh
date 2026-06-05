#!/usr/bin/env bash
set -euo pipefail

ENVIRONMENT="${1:?Usage: publish.sh <dev|pre|prod>}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEFAULT_PARAMS="${ROOT}/infrastructure/parameters.${ENVIRONMENT}.json"
[[ -f "${DEFAULT_PARAMS}" ]] || DEFAULT_PARAMS="${ROOT}/infrastructure/parameters.json"
PARAMS="${PARAMS_FILE:-${DEFAULT_PARAMS}}"
CONFIG_FILE="${CONFIG_FILE:-${ROOT}/config/data-agent.yaml}"
DATA_AGENT_INSTANCE="${DATA_AGENT_INSTANCE:-data-agent}"
VERSION="${VERSION:-$(date -u +%Y%m%d%H%M%S)}"
MANIFEST_DIR="${ROOT}/dist/manifests"

python3 "${ROOT}/scripts/validate_parameters.py" "${PARAMS}" "${ENVIRONMENT}" "${CONFIG_FILE}" "${DATA_AGENT_INSTANCE}"

BUCKET="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["artifact_bucket_name"])' "${PARAMS}")"
if [[ "${DATA_AGENT_INSTANCE}" == "data-agent" ]]; then
  ARTIFACT_PREFIX="artifacts/${ENVIRONMENT}"
  CONFIG_PREFIX="config/${ENVIRONMENT}"
  MANIFEST_PREFIX="manifests/${ENVIRONMENT}"
else
  ARTIFACT_PREFIX="artifacts/${ENVIRONMENT}/${DATA_AGENT_INSTANCE}"
  CONFIG_PREFIX="config/${ENVIRONMENT}/${DATA_AGENT_INSTANCE}"
  MANIFEST_PREFIX="manifests/${ENVIRONMENT}/${DATA_AGENT_INSTANCE}"
fi
ARTIFACT_KEY="${ARTIFACT_PREFIX}/data-agent-${VERSION}.zip"
CONFIG_KEY="${CONFIG_PREFIX}/data-agent-${VERSION}.yaml"
MANIFEST_KEY="${MANIFEST_PREFIX}/data-agent-${VERSION}.json"
ACTIVE_MANIFEST_KEY="${MANIFEST_PREFIX}/active.json"
SHA256="$(shasum -a 256 "${ROOT}/dist/data-agent.zip" | awk '{print $1}')"

mkdir -p "${MANIFEST_DIR}"
MANIFEST_FILE="${MANIFEST_DIR}/${DATA_AGENT_INSTANCE}-data-agent-${VERSION}.json"
python3 - "${MANIFEST_FILE}" "${VERSION}" "${ARTIFACT_KEY}" "${CONFIG_KEY}" "${SHA256}" "${DATA_AGENT_INSTANCE}" <<'PY'
import json
import sys

path, version, artifact_key, config_key, sha256, instance = sys.argv[1:]
with open(path, "w", encoding="utf-8") as handle:
    json.dump(
        {
            "active_version": version,
            "instance": instance,
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
aws s3 cp "${CONFIG_FILE}" "s3://${BUCKET}/${CONFIG_KEY}" \
  --only-show-errors
aws s3 cp "${MANIFEST_FILE}" "s3://${BUCKET}/${MANIFEST_KEY}" \
  --only-show-errors
aws s3 cp "${MANIFEST_FILE}" "s3://${BUCKET}/${ACTIVE_MANIFEST_KEY}" \
  --only-show-errors

echo "DATA_AGENT_INSTANCE=${DATA_AGENT_INSTANCE}"
echo "ARTIFACT_KEY=${ARTIFACT_KEY}"
echo "CONFIG_KEY=${CONFIG_KEY}"
echo "MANIFEST_KEY=${MANIFEST_KEY}"
