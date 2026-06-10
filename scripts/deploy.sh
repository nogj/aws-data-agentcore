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
SKIP_TARGET_DEPLOY="${SKIP_TARGET_DEPLOY:-false}"

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

merge_csv() {
  python3 - "$@" <<'PY'
import sys

seen: set[str] = set()
merged: list[str] = []
for raw in sys.argv[1:]:
    for item in raw.split(","):
        value = item.strip()
        if value and value not in seen:
            seen.add(value)
            merged.append(value)
print(",".join(merged))
PY
}

REGION="$(read_param region)"
BUCKET="$(read_param artifact_bucket_name)"
ALLOWED_REQUEST_HEADERS="${ALLOWED_REQUEST_HEADERS:-$(read_optional_agent_param allowed_request_headers)}"
ALLOWED_REQUEST_HEADERS="${ALLOWED_REQUEST_HEADERS:-Mcp-Session-Id,x-data-agent-context}"
ALLOWED_RESPONSE_HEADERS="${ALLOWED_RESPONSE_HEADERS:-$(read_optional_agent_param allowed_response_headers)}"
ALLOWED_RESPONSE_HEADERS="${ALLOWED_RESPONSE_HEADERS:-Mcp-Session-Id}"
IDLE_RUNTIME_SESSION_TIMEOUT="$(read_optional_agent_param idle_runtime_session_timeout)"
IDLE_RUNTIME_SESSION_TIMEOUT="${IDLE_RUNTIME_SESSION_TIMEOUT:-300}"
MAX_LIFETIME="$(read_optional_agent_param max_lifetime)"
MAX_LIFETIME="${MAX_LIFETIME:-3600}"
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
FOUNDATION_STACK="data-agent-foundation-${ENVIRONMENT}${STACK_SUFFIX}"

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

foundation_output() {
  aws cloudformation describe-stacks \
    --region "${REGION}" \
    --stack-name "${FOUNDATION_STACK}" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" \
    --output text
}

write_secret_value() {
  local secret_arn="$1"
  local secret_string="$2"
  local secret_file
  secret_file="$(mktemp)"
  printf '%s' "${secret_string}" >"${secret_file}"
  aws secretsmanager update-secret \
    --region "${REGION}" \
    --secret-id "${secret_arn}" \
    --secret-string "file://${secret_file}" >/dev/null
  rm -f "${secret_file}"
}

RUNTIME_NETWORK_MODE="$(read_optional_agent_param runtime_network_mode)"
RUNTIME_NETWORK_MODE="${RUNTIME_NETWORK_MODE:-external}"
DATABASE_SECRET_MODE="$(read_optional_agent_param database_secret_mode)"
DATABASE_SECRET_MODE="${DATABASE_SECRET_MODE:-external}"

if [[ "${RUNTIME_NETWORK_MODE}" == "managed" || "${DATABASE_SECRET_MODE}" == "managed" ]]; then
  DATABASE_SECRET_NAME="$(read_optional_agent_param database_secret_name)"
  DATABASE_SECRET_STRING="$(read_optional_agent_param database_secret_string)"
  DATABASE_SECRET_STRING="${DATABASE_SECRET_STRING:-{}}"
  RUNTIME_SECURITY_GROUP_NAME="$(read_optional_agent_param runtime_security_group_name)"
  FOUNDATION_VPC_ID="$(read_optional_agent_param vpc_id)"
  aws cloudformation deploy \
    --region "${REGION}" \
    --stack-name "${FOUNDATION_STACK}" \
    --template-file "${ROOT}/infrastructure/agent-foundation.yaml" \
    --parameter-overrides \
      Environment="${ENVIRONMENT}" \
      AgentName="${DATA_AGENT_INSTANCE}" \
      VpcId="${FOUNDATION_VPC_ID}" \
      RuntimeNetworkMode="${RUNTIME_NETWORK_MODE}" \
      ManagedPrivateSubnetCidr1="$(read_optional_agent_param managed_private_subnet_cidr_1)" \
      ManagedPrivateSubnetCidr2="$(read_optional_agent_param managed_private_subnet_cidr_2)" \
      RuntimeSecurityGroupName="${RUNTIME_SECURITY_GROUP_NAME}" \
      DatabaseSecretMode="${DATABASE_SECRET_MODE}" \
      DatabaseSecretName="${DATABASE_SECRET_NAME}"
fi

if [[ "${RUNTIME_NETWORK_MODE}" == "managed" ]]; then
  EFFECTIVE_PRIVATE_SUBNET_IDS="$(foundation_output PrivateSubnetIds)"
  EFFECTIVE_RUNTIME_SECURITY_GROUP_IDS="$(foundation_output RuntimeSecurityGroupIds)"
  EFFECTIVE_RUNTIME_ROUTE_TABLE_IDS="$(foundation_output RuntimeRouteTableIds)"
else
  EFFECTIVE_PRIVATE_SUBNET_IDS="$(read_agent_param private_subnet_ids)"
  EFFECTIVE_RUNTIME_SECURITY_GROUP_IDS="$(read_agent_param runtime_security_group_ids)"
  EFFECTIVE_RUNTIME_ROUTE_TABLE_IDS="$(read_optional_agent_param runtime_route_table_ids)"
fi

if [[ "${DATABASE_SECRET_MODE}" == "managed" ]]; then
  EFFECTIVE_DATABASE_SECRET_ARN="$(foundation_output DatabaseSecretArn)"
  DATABASE_SECRET_STRING="$(read_optional_agent_param database_secret_string)"
  if [[ -n "${DATABASE_SECRET_STRING}" ]]; then
    write_secret_value "${EFFECTIVE_DATABASE_SECRET_ARN}" "${DATABASE_SECRET_STRING}"
  fi
else
  EFFECTIVE_DATABASE_SECRET_ARN="$(read_agent_param database_secret_arn)"
fi

endpoint_output="$(
  DATA_AGENT_INSTANCE="${DATA_AGENT_INSTANCE}" \
  CONFIG_FILE="${CONFIG_FILE}" \
  PARAMS_FILE="${PARAMS}" \
  EFFECTIVE_PRIVATE_SUBNET_IDS="${EFFECTIVE_PRIVATE_SUBNET_IDS}" \
  EFFECTIVE_RUNTIME_SECURITY_GROUP_IDS="${EFFECTIVE_RUNTIME_SECURITY_GROUP_IDS}" \
  EFFECTIVE_RUNTIME_ROUTE_TABLE_IDS="${EFFECTIVE_RUNTIME_ROUTE_TABLE_IDS}" \
  "${ROOT}/scripts/deploy_private_endpoints.sh" "${ENVIRONMENT}"
)"
printf '%s\n' "${endpoint_output}"
RUNTIME_ACCESS_SECURITY_GROUP_ID="$(printf '%s\n' "${endpoint_output}" | awk -F= '/^RUNTIME_ACCESS_SECURITY_GROUP_ID=/{print $2}')"
if [[ -n "${RUNTIME_ACCESS_SECURITY_GROUP_ID}" && "${RUNTIME_ACCESS_SECURITY_GROUP_ID}" != "None" ]]; then
  EFFECTIVE_RUNTIME_SECURITY_GROUP_IDS="$(merge_csv "${EFFECTIVE_RUNTIME_SECURITY_GROUP_IDS}" "${RUNTIME_ACCESS_SECURITY_GROUP_ID}")"
fi

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
    DatabaseSecretArn="${EFFECTIVE_DATABASE_SECRET_ARN}" \
    HeaderSigningSecretArn="${HEADER_SIGNING_SECRET_ARN}" \
    InternalContextAudience="runtime:${TARGET_NAME}" \
    OpenAISecretArn="$(read_optional_agent_param openai_secret_arn)" \
    PrivateSubnetIds="${EFFECTIVE_PRIVATE_SUBNET_IDS}" \
    RuntimeSecurityGroupIds="${EFFECTIVE_RUNTIME_SECURITY_GROUP_IDS}" \
    AgentRuntimeName="${AGENT_RUNTIME_NAME}" \
    IdleRuntimeSessionTimeout="${IDLE_RUNTIME_SESSION_TIMEOUT}" \
    MaxLifetime="${MAX_LIFETIME}"

RUNTIME_ID="$(aws cloudformation describe-stacks --region "${REGION}" --stack-name "${RUNTIME_STACK}" --query "Stacks[0].Outputs[?OutputKey=='RuntimeId'].OutputValue" --output text)"
RUNTIME_ARN="$(aws bedrock-agentcore-control get-agent-runtime --region "${REGION}" --agent-runtime-id "${RUNTIME_ID}" --query agentRuntimeArn --output text)"
RUNTIME_ENDPOINT_URL="$(python3 -c 'import sys,urllib.parse; print("https://bedrock-agentcore.{}.amazonaws.com/runtimes/{}/invocations?qualifier=DEFAULT".format(sys.argv[1], urllib.parse.quote(sys.argv[2], safe="")))' "${REGION}" "${RUNTIME_ARN}")"

if [[ "${SKIP_TARGET_DEPLOY}" == "true" ]]; then
  echo "Skipped target deployment for ${DATA_AGENT_INSTANCE}; runtime ${RUNTIME_ARN}"
  exit 0
fi

aws cloudformation deploy \
  --region "${REGION}" \
  --stack-name "${TARGET_STACK}" \
  --template-file "${ROOT}/infrastructure/target.yaml" \
  --parameter-overrides \
    GatewayIdentifier="${GATEWAY_ID}" \
    TargetName="${TARGET_NAME}" \
    TargetDescription="${TARGET_DESCRIPTION}" \
    RuntimeEndpointUrl="${RUNTIME_ENDPOINT_URL}" \
    AllowedRequestHeaders="${ALLOWED_REQUEST_HEADERS}" \
    AllowedResponseHeaders="${ALLOWED_RESPONSE_HEADERS}"

echo "Deployed ${DATA_AGENT_INSTANCE} runtime ${RUNTIME_ARN}"
