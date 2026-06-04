#!/usr/bin/env bash
set -euo pipefail

ENVIRONMENT="${1:?Usage: smoke_test.sh <dev|pre|prod>}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEFAULT_PARAMS="${ROOT}/infrastructure/parameters.${ENVIRONMENT}.json"
[[ -f "${DEFAULT_PARAMS}" ]] || DEFAULT_PARAMS="${ROOT}/infrastructure/parameters.json"
PARAMS="${PARAMS_FILE:-${DEFAULT_PARAMS}}"
TOKEN="${BEARER_TOKEN:?Set BEARER_TOKEN}"
QUESTION="${SMOKE_QUESTION:-List one visible CI name and id.}"
python3 "${ROOT}/scripts/validate_parameters.py" "${PARAMS}" "${ENVIRONMENT}" "${ROOT}/config/data-agent.yaml"
REGION="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["region"])' "${PARAMS}")"
GATEWAY_URL="$(aws cloudformation describe-stacks --region "${REGION}" --stack-name "data-agent-bootstrap-${ENVIRONMENT}" --query "Stacks[0].Outputs[?OutputKey=='GatewayUrl'].OutputValue" --output text)"

python3 "${ROOT}/scripts/smoke_test.py" \
  --gateway-url "${GATEWAY_URL}" \
  --token "${TOKEN}" \
  --question "${QUESTION}"
