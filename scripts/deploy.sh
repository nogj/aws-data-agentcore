#!/usr/bin/env bash
set -euo pipefail

ENVIRONMENT="${1:?Usage: deploy.sh <dev|pre|prod>}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEFAULT_PARAMS="${ROOT}/infrastructure/parameters.${ENVIRONMENT}.json"
[[ -f "${DEFAULT_PARAMS}" ]] || DEFAULT_PARAMS="${ROOT}/infrastructure/parameters.json"
PARAMS="${PARAMS_FILE:-${DEFAULT_PARAMS}}"
CONFIG_FILE="${CONFIG_FILE:-${ROOT}/config/data-agent.yaml}"
DATA_AGENT_INSTANCE="${DATA_AGENT_INSTANCE:-data-agent}"
TARGET_NAME="${TARGET_NAME:-${DATA_AGENT_INSTANCE}}"
TARGET_DESCRIPTION="${TARGET_DESCRIPTION:-Public MCP target for ${TARGET_NAME}.}"
ARTIFACT_KEY="${ARTIFACT_KEY:?Set ARTIFACT_KEY from publish.sh output}"
CONFIG_KEY="${CONFIG_KEY:?Set CONFIG_KEY from publish.sh output}"

python3 "${ROOT}/scripts/validate_parameters.py" "${PARAMS}" "${ENVIRONMENT}" "${CONFIG_FILE}" "${DATA_AGENT_INSTANCE}"

read_param() {
  python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])' "${PARAMS}" "$1"
}

read_agent_param() {
  python3 -c 'import json,sys; data=json.load(open(sys.argv[1])); instance=sys.argv[2]; key=sys.argv[3]; agent=data.get("agents", {}).get(instance, {}); print(agent[key] if key in agent else data[key])' "${PARAMS}" "${DATA_AGENT_INSTANCE}" "$1"
}

read_optional_param() {
  python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get(sys.argv[2], ""))' "${PARAMS}" "$1"
}

read_optional_agent_param() {
  python3 -c 'import json,sys; data=json.load(open(sys.argv[1])); instance=sys.argv[2]; key=sys.argv[3]; agent=data.get("agents", {}).get(instance, {}); print(agent[key] if key in agent else data.get(key, ""))' "${PARAMS}" "${DATA_AGENT_INSTANCE}" "$1"
}

REGION="$(read_param region)"
BUCKET="$(read_param artifact_bucket_name)"
ALLOWED_REQUEST_HEADERS="${ALLOWED_REQUEST_HEADERS:-$(read_optional_agent_param allowed_request_headers)}"
ALLOWED_REQUEST_HEADERS="${ALLOWED_REQUEST_HEADERS:-x-data-agent-grants,x-data-agent-identity}"
BOOTSTRAP_STACK="data-agent-bootstrap-${ENVIRONMENT}"
if [[ "${DATA_AGENT_INSTANCE}" == "data-agent" ]]; then
  STACK_SUFFIX=""
  AGENT_RUNTIME_NAME=""
else
  STACK_SUFFIX="-${DATA_AGENT_INSTANCE}"
  AGENT_RUNTIME_NAME="$(python3 -c 'import re,sys; print("read_only_data_agent_{}_{}".format(sys.argv[1], re.sub(r"[^A-Za-z0-9_]", "_", sys.argv[2]))[:64])' "${ENVIRONMENT}" "${DATA_AGENT_INSTANCE}")"
fi
RUNTIME_ROLE_NAME="$(python3 -c 'import re,sys; print("data-agent-runtime-{}-{}".format(sys.argv[1], re.sub(r"[^A-Za-z0-9_+=,.@-]", "-", sys.argv[2]))[:64])' "${ENVIRONMENT}" "${DATA_AGENT_INSTANCE}")"
RUNTIME_STACK="data-agent-runtime-${ENVIRONMENT}${STACK_SUFFIX}"
TARGET_STACK="data-agent-target-${ENVIRONMENT}${STACK_SUFFIX}"

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
    AuthorizationMode="$(python3 -c 'import sys,yaml; print(yaml.safe_load(open(sys.argv[1]))["authorization"]["mode"])' "${CONFIG_FILE}")" \
    AcceptedClaims="$(python3 -c 'import sys,yaml; print(",".join(yaml.safe_load(open(sys.argv[1]))["authorization"]["accepted_claims"]))' "${CONFIG_FILE}")" \
    IdentityClaims="$(python3 -c 'import sys,yaml; print(",".join(yaml.safe_load(open(sys.argv[1]))["authorization"].get("identity_claims", [])))' "${CONFIG_FILE}")"

GATEWAY_ID="$(aws cloudformation describe-stacks --region "${REGION}" --stack-name "${BOOTSTRAP_STACK}" --query "Stacks[0].Outputs[?OutputKey=='GatewayIdentifier'].OutputValue" --output text)"
HEADER_SIGNING_SECRET_ARN="$(aws cloudformation describe-stacks --region "${REGION}" --stack-name "${BOOTSTRAP_STACK}" --query "Stacks[0].Outputs[?OutputKey=='HeaderSigningSecretArn'].OutputValue" --output text)"

aws cloudformation deploy \
  --region "${REGION}" \
  --stack-name "${RUNTIME_STACK}" \
  --template-file "${ROOT}/infrastructure/runtime.yaml" \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
    Environment="${ENVIRONMENT}" \
    ArtifactBucketName="${BUCKET}" \
    ArtifactKey="${ARTIFACT_KEY}" \
    ConfigKey="${CONFIG_KEY}" \
    RuntimeRoleName="${RUNTIME_ROLE_NAME}" \
    DatabaseSecretArn="$(read_agent_param database_secret_arn)" \
    HeaderSigningSecretArn="${HEADER_SIGNING_SECRET_ARN}" \
    OpenAISecretArn="$(read_optional_agent_param openai_secret_arn)" \
    PrivateSubnetIds="$(read_agent_param private_subnet_ids)" \
    RuntimeSecurityGroupIds="$(read_agent_param runtime_security_group_ids)" \
    AgentRuntimeName="${AGENT_RUNTIME_NAME}"

RUNTIME_ARN="$(aws cloudformation describe-stacks --region "${REGION}" --stack-name "${RUNTIME_STACK}" --query "Stacks[0].Outputs[?OutputKey=='RuntimeArn'].OutputValue" --output text)"
RUNTIME_ENDPOINT_URL="$(python3 -c 'import sys,urllib.parse; print("https://bedrock-agentcore.{}.amazonaws.com/runtimes/{}/invocations?qualifier=DEFAULT".format(sys.argv[1], urllib.parse.quote(sys.argv[2], safe="")))' "${REGION}" "${RUNTIME_ARN}")"

aws cloudformation deploy \
  --region "${REGION}" \
  --stack-name "${TARGET_STACK}" \
  --template-file "${ROOT}/infrastructure/target.yaml" \
  --parameter-overrides \
    GatewayIdentifier="${GATEWAY_ID}" \
    TargetName="${TARGET_NAME}" \
    TargetDescription="${TARGET_DESCRIPTION}" \
    RuntimeEndpointUrl="${RUNTIME_ENDPOINT_URL}" \
    AllowedRequestHeaders="${ALLOWED_REQUEST_HEADERS}"

echo "Deployed ${DATA_AGENT_INSTANCE} runtime ${RUNTIME_ARN}"
