#!/usr/bin/env bash
set -euo pipefail

ENVIRONMENT="${1:?Usage: agent_cli.sh <dev|pre|prod>}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEFAULT_PARAMS="${ROOT}/infrastructure/parameters.${ENVIRONMENT}.json"
[[ -f "${DEFAULT_PARAMS}" ]] || DEFAULT_PARAMS="${ROOT}/infrastructure/parameters.json"
PARAMS="${PARAMS_FILE:-${DEFAULT_PARAMS}}"
CONFIG_FILE="${CONFIG_FILE:-${ROOT}/config/data-agent.yaml}"
DATA_AGENT_INSTANCE="${DATA_AGENT_INSTANCE:-data-agent}"
TARGET_NAME="${TARGET_NAME:-${DATA_AGENT_INSTANCE}}"
TOKEN="${BEARER_TOKEN:?Set BEARER_TOKEN}"

python3 "${ROOT}/scripts/validate_parameters.py" "${PARAMS}" "${ENVIRONMENT}" "${CONFIG_FILE}" "${DATA_AGENT_INSTANCE}"
REGION="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["region"])' "${PARAMS}")"
GATEWAY_URL="$(aws cloudformation describe-stacks --region "${REGION}" --stack-name "data-agent-bootstrap-${ENVIRONMENT}" --query "Stacks[0].Outputs[?OutputKey=='GatewayUrl'].OutputValue" --output text)"

python3 "${ROOT}/scripts/agent_cli.py" \
  --gateway-url "${GATEWAY_URL}" \
  --token "${TOKEN}" \
  --target-name "${TARGET_NAME}"
