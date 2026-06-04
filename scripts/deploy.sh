#!/usr/bin/env bash
set -euo pipefail

ENVIRONMENT="${1:?Usage: deploy.sh <dev|pre|prod>}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEFAULT_PARAMS="${ROOT}/infrastructure/parameters.${ENVIRONMENT}.json"
[[ -f "${DEFAULT_PARAMS}" ]] || DEFAULT_PARAMS="${ROOT}/infrastructure/parameters.json"
PARAMS="${PARAMS_FILE:-${DEFAULT_PARAMS}}"
ARTIFACT_KEY="${ARTIFACT_KEY:?Set ARTIFACT_KEY from publish.sh output}"
CONFIG_KEY="${CONFIG_KEY:?Set CONFIG_KEY from publish.sh output}"

python3 "${ROOT}/scripts/validate_parameters.py" "${PARAMS}" "${ENVIRONMENT}" "${ROOT}/config/data-agent.yaml"

read_param() {
  python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])' "${PARAMS}" "$1"
}

read_optional_param() {
  python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get(sys.argv[2], ""))' "${PARAMS}" "$1"
}

REGION="$(read_param region)"
BUCKET="$(read_param artifact_bucket_name)"
BOOTSTRAP_STACK="data-agent-bootstrap-${ENVIRONMENT}"
RUNTIME_STACK="data-agent-runtime-${ENVIRONMENT}"
TARGET_STACK="data-agent-target-${ENVIRONMENT}"

aws cloudformation deploy \
  --region "${REGION}" \
  --stack-name "${BOOTSTRAP_STACK}" \
  --template-file "${ROOT}/infrastructure/bootstrap.yaml" \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
    Environment="${ENVIRONMENT}" \
    ArtifactBucketName="${BUCKET}" \
    JwtDiscoveryUrl="$(read_param jwt_discovery_url)" \
    JwtAllowedAudience="$(read_param jwt_allowed_audience)" \
    RequiredScope="$(read_param required_scope)" \
    AcceptedClaims="$(python3 -c 'import sys,yaml; print(",".join(yaml.safe_load(open(sys.argv[1]))["authorization"]["accepted_claims"]))' "${ROOT}/config/data-agent.yaml")"

RUNTIME_ROLE_ARN="$(aws cloudformation describe-stacks --region "${REGION}" --stack-name "${BOOTSTRAP_STACK}" --query "Stacks[0].Outputs[?OutputKey=='RuntimeRoleArn'].OutputValue" --output text)"
GATEWAY_ID="$(aws cloudformation describe-stacks --region "${REGION}" --stack-name "${BOOTSTRAP_STACK}" --query "Stacks[0].Outputs[?OutputKey=='GatewayIdentifier'].OutputValue" --output text)"

aws cloudformation deploy \
  --region "${REGION}" \
  --stack-name "${RUNTIME_STACK}" \
  --template-file "${ROOT}/infrastructure/runtime.yaml" \
  --parameter-overrides \
    Environment="${ENVIRONMENT}" \
    ArtifactBucketName="${BUCKET}" \
    ArtifactKey="${ARTIFACT_KEY}" \
    ConfigKey="${CONFIG_KEY}" \
    RuntimeRoleArn="${RUNTIME_ROLE_ARN}" \
    DatabaseSecretArn="$(read_param database_secret_arn)" \
    OpenAISecretArn="$(read_optional_param openai_secret_arn)" \
    PrivateSubnetIds="$(read_param private_subnet_ids)" \
    RuntimeSecurityGroupIds="$(read_param runtime_security_group_ids)"

RUNTIME_ARN="$(aws cloudformation describe-stacks --region "${REGION}" --stack-name "${RUNTIME_STACK}" --query "Stacks[0].Outputs[?OutputKey=='RuntimeArn'].OutputValue" --output text)"
RUNTIME_ENDPOINT_URL="$(python3 -c 'import sys,urllib.parse; print("https://bedrock-agentcore.{}.amazonaws.com/runtimes/{}/invocations?qualifier=DEFAULT".format(sys.argv[1], urllib.parse.quote(sys.argv[2], safe="")))' "${REGION}" "${RUNTIME_ARN}")"

aws cloudformation deploy \
  --region "${REGION}" \
  --stack-name "${TARGET_STACK}" \
  --template-file "${ROOT}/infrastructure/target.yaml" \
  --parameter-overrides \
    GatewayIdentifier="${GATEWAY_ID}" \
    RuntimeEndpointUrl="${RUNTIME_ENDPOINT_URL}"

echo "Deployed runtime ${RUNTIME_ARN}"
