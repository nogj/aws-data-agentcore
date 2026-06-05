#!/usr/bin/env bash
set -euo pipefail

ENVIRONMENT="${1:?Usage: bootstrap.sh <dev|pre|prod>}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEFAULT_PARAMS="${ROOT}/infrastructure/parameters.${ENVIRONMENT}.json"
[[ -f "${DEFAULT_PARAMS}" ]] || DEFAULT_PARAMS="${ROOT}/infrastructure/parameters.json"
PARAMS="${PARAMS_FILE:-${DEFAULT_PARAMS}}"
CONFIG_FILE="${CONFIG_FILE:-${ROOT}/config/data-agent.yaml}"

python3 "${ROOT}/scripts/validate_parameters.py" "${PARAMS}" "${ENVIRONMENT}" "${CONFIG_FILE}"
python3 "${ROOT}/scripts/preflight_aws.py" \
  --parameters "${PARAMS}" \
  --config "${CONFIG_FILE}"

read_param() {
  python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])' "${PARAMS}" "$1"
}

REGION="$(read_param region)"
aws cloudformation deploy \
  --region "${REGION}" \
  --stack-name "data-agent-bootstrap-${ENVIRONMENT}" \
  --template-file "${ROOT}/infrastructure/bootstrap.yaml" \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
    Environment="${ENVIRONMENT}" \
    ArtifactBucketName="$(read_param artifact_bucket_name)" \
    JwtDiscoveryUrl="$(read_param jwt_discovery_url)" \
    JwtAllowedAudience="$(read_param jwt_allowed_audience)" \
    RequiredScope="$(read_param required_scope)" \
    AuthorizationMode="$(python3 -c 'import sys,yaml; print(yaml.safe_load(open(sys.argv[1]))["authorization"]["mode"])' "${CONFIG_FILE}")" \
    AcceptedClaims="$(python3 -c 'import sys,yaml; print(",".join(yaml.safe_load(open(sys.argv[1]))["authorization"]["accepted_claims"]))' "${CONFIG_FILE}")" \
    IdentityClaims="$(python3 -c 'import sys,yaml; print(",".join(yaml.safe_load(open(sys.argv[1]))["authorization"].get("identity_claims", [])))' "${CONFIG_FILE}")"
