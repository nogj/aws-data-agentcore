#!/usr/bin/env bash
set -euo pipefail

ENVIRONMENT="${1:?Usage: deploy_private_endpoints.sh <dev|pre|prod>}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEFAULT_PARAMS="${ROOT}/infrastructure/parameters.${ENVIRONMENT}.json"
[[ -f "${DEFAULT_PARAMS}" ]] || DEFAULT_PARAMS="${ROOT}/infrastructure/parameters.json"
PARAMS="${PARAMS_FILE:-${DEFAULT_PARAMS}}"
CONFIG_FILE="${CONFIG_FILE:-${ROOT}/config/data-agent.yaml}"
DATA_AGENT_INSTANCE="${DATA_AGENT_INSTANCE:-data-agent}"

python3 "${ROOT}/scripts/validate_parameters.py" "${PARAMS}" "${ENVIRONMENT}" "${CONFIG_FILE}" "${DATA_AGENT_INSTANCE}"

read_agent_param() {
  python3 -c 'import json,sys; data=json.load(open(sys.argv[1])); instance=sys.argv[2]; key=sys.argv[3]; agent=data.get("agents", {}).get(instance, {}); print(agent[key] if key in agent else data[key])' "${PARAMS}" "${DATA_AGENT_INSTANCE}" "$1"
}

read_optional_agent_param() {
  python3 -c 'import json,sys; data=json.load(open(sys.argv[1])); instance=sys.argv[2]; key=sys.argv[3]; agent=data.get("agents", {}).get(instance, {}); print(agent[key] if key in agent else data.get(key, ""))' "${PARAMS}" "${DATA_AGENT_INSTANCE}" "$1"
}

derive_route_tables() {
  local region="$1"
  local vpc_id="$2"
  local subnet_csv="$3"
  python3 - "${region}" "${vpc_id}" "${subnet_csv}" <<'PY'
import json
import subprocess
import sys

region, vpc_id, subnet_csv = sys.argv[1:4]
subnets = [item.strip() for item in subnet_csv.split(",") if item.strip()]
route_tables: list[str] = []

def aws_json(*args: str) -> dict:
    out = subprocess.check_output(["aws", "--region", region, *args], text=True)
    return json.loads(out)

main = None

for subnet_id in subnets:
    data = aws_json(
        "ec2",
        "describe-route-tables",
        "--filters",
        f"Name=association.subnet-id,Values={subnet_id}",
    )
    tables = data.get("RouteTables", [])
    if tables:
        route_table = tables[0]["RouteTableId"]
    else:
        if main is None:
            main_data = aws_json(
                "ec2",
                "describe-route-tables",
                "--filters",
                f"Name=vpc-id,Values={vpc_id}",
                "Name=association.main,Values=true",
            )
            main = main_data["RouteTables"][0]["RouteTableId"]
        route_table = main
    if route_table not in route_tables:
        route_tables.append(route_table)

print(",".join(route_tables))
PY
}

default_endpoint_stack_name() {
  local environment="$1"
  local vpc_id="$2"
  python3 - "${environment}" "${vpc_id}" <<'PY'
import re
import sys

environment, vpc_id = sys.argv[1:3]
safe_vpc = re.sub(r"[^A-Za-z0-9-]", "-", vpc_id)
print(f"data-agent-private-endpoints-{environment}-{safe_vpc}"[:128])
PY
}

find_product_endpoint_stack() {
  local region="$1"
  local environment="$2"
  local vpc_id="$3"
  local fallback_stack="$4"
  python3 - "${region}" "${environment}" "${vpc_id}" "${fallback_stack}" <<'PY'
import json
import subprocess
import sys

region, environment, vpc_id, fallback_stack = sys.argv[1:5]
prefix = f"data-agent-private-endpoints-{environment}"
active = "CREATE_COMPLETE,CREATE_IN_PROGRESS,UPDATE_COMPLETE,UPDATE_IN_PROGRESS,UPDATE_ROLLBACK_COMPLETE,IMPORT_COMPLETE,IMPORT_IN_PROGRESS"

def describe_named(name: str) -> dict | None:
    try:
        out = subprocess.check_output(
            [
                "aws",
                "--region",
                region,
                "cloudformation",
                "describe-stacks",
                "--stack-name",
                name,
                "--output",
                "json",
            ],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        return None
    stacks = json.loads(out).get("Stacks", [])
    return stacks[0] if stacks else None

fallback = describe_named(fallback_stack)
if fallback is not None:
    print(fallback_stack)
    raise SystemExit(0)

out = subprocess.check_output(
    [
        "aws",
        "--region",
        region,
        "cloudformation",
        "list-stacks",
        "--stack-status-filter",
        *active.split(","),
        "--output",
        "json",
    ],
    text=True,
)
names = sorted(
    item["StackName"]
    for item in json.loads(out).get("StackSummaries", [])
    if item["StackName"].startswith(prefix)
)

for name in names:
    stack = describe_named(name)
    if stack is None:
        continue
    params = {
        item["ParameterKey"]: item.get("ParameterValue", "")
        for item in stack.get("Parameters", [])
    }
    if params.get("VpcId") == vpc_id:
        print(name)
        raise SystemExit(0)

print(fallback_stack)
PY
}

stack_parameter() {
  local region="$1"
  local stack_name="$2"
  local parameter_key="$3"
  aws cloudformation describe-stacks \
    --region "${region}" \
    --stack-name "${stack_name}" \
    --query "Stacks[0].Parameters[?ParameterKey=='${parameter_key}'].ParameterValue" \
    --output text 2>/dev/null || true
}

stack_output() {
  local region="$1"
  local stack_name="$2"
  local output_key="$3"
  aws cloudformation describe-stacks \
    --region "${region}" \
    --stack-name "${stack_name}" \
    --query "Stacks[0].Outputs[?OutputKey=='${output_key}'].OutputValue" \
    --output text
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

REGION="$(read_agent_param region)"
CREATE_PRIVATE_SERVICE_ENDPOINTS="$(read_optional_agent_param create_private_service_endpoints)"
CREATE_PRIVATE_SERVICE_ENDPOINTS="${CREATE_PRIVATE_SERVICE_ENDPOINTS:-true}"
CREATE_PRIVATE_SERVICE_ENDPOINTS="$(python3 -c 'import sys; print(str(sys.argv[1]).lower())' "${CREATE_PRIVATE_SERVICE_ENDPOINTS}")"

if [[ "${CREATE_PRIVATE_SERVICE_ENDPOINTS}" != "true" ]]; then
  echo "Private service endpoint stack disabled for ${DATA_AGENT_INSTANCE}."
  echo "PRIVATE_SERVICE_ENDPOINT_STACK_NAME="
  echo "RUNTIME_ACCESS_SECURITY_GROUP_ID="
  exit 0
fi

VPC_ID="$(read_agent_param vpc_id)"
PRIVATE_SERVICE_ENDPOINT_STACK_NAME="$(read_optional_agent_param private_service_endpoint_stack_name)"
PRIVATE_SERVICE_ENDPOINT_STACK_NAME="${PRIVATE_SERVICE_ENDPOINT_STACK_NAME:-$(find_product_endpoint_stack "${REGION}" "${ENVIRONMENT}" "${VPC_ID}" "$(default_endpoint_stack_name "${ENVIRONMENT}" "${VPC_ID}")")}"
EXISTING_ENDPOINT_STACK_VPC="$(stack_parameter "${REGION}" "${PRIVATE_SERVICE_ENDPOINT_STACK_NAME}" VpcId)"
RUNTIME_SUBNET_IDS="${EFFECTIVE_PRIVATE_SUBNET_IDS:-$(read_agent_param private_subnet_ids)}"
ENDPOINT_SUBNET_IDS="$(read_optional_agent_param endpoint_subnet_ids)"
if [[ -z "${ENDPOINT_SUBNET_IDS}" && "${EXISTING_ENDPOINT_STACK_VPC}" == "${VPC_ID}" ]]; then
  ENDPOINT_SUBNET_IDS="$(stack_parameter "${REGION}" "${PRIVATE_SERVICE_ENDPOINT_STACK_NAME}" EndpointSubnetIds)"
fi
ENDPOINT_SUBNET_IDS="${ENDPOINT_SUBNET_IDS:-${RUNTIME_SUBNET_IDS}}"
RUNTIME_SECURITY_GROUP_IDS="${EFFECTIVE_RUNTIME_SECURITY_GROUP_IDS:-$(read_agent_param runtime_security_group_ids)}"
RUNTIME_ROUTE_TABLE_IDS="$(read_optional_agent_param runtime_route_table_ids)"
RUNTIME_ROUTE_TABLE_IDS="${EFFECTIVE_RUNTIME_ROUTE_TABLE_IDS:-${RUNTIME_ROUTE_TABLE_IDS}}"
RUNTIME_ROUTE_TABLE_IDS="${RUNTIME_ROUTE_TABLE_IDS:-$(derive_route_tables "${REGION}" "${VPC_ID}" "${RUNTIME_SUBNET_IDS}")}"
if [[ "${EXISTING_ENDPOINT_STACK_VPC}" == "${VPC_ID}" ]]; then
  EXISTING_RUNTIME_ROUTE_TABLE_IDS="$(stack_parameter "${REGION}" "${PRIVATE_SERVICE_ENDPOINT_STACK_NAME}" RuntimeRouteTableIds)"
  RUNTIME_ROUTE_TABLE_IDS="$(merge_csv "${EXISTING_RUNTIME_ROUTE_TABLE_IDS}" "${RUNTIME_ROUTE_TABLE_IDS}")"
fi
ENDPOINT_INGRESS_CIDR="$(read_optional_agent_param endpoint_ingress_cidr)"
ENDPOINT_SECURITY_GROUP_NAME="$(read_optional_agent_param endpoint_security_group_name)"
ENDPOINT_SECURITY_GROUP_NAME="${ENDPOINT_SECURITY_GROUP_NAME:-data-agent-runtime-vpc-endpoints}"
RUNTIME_ACCESS_SECURITY_GROUP_NAME="$(read_optional_agent_param runtime_access_security_group_name)"
RUNTIME_ACCESS_SECURITY_GROUP_NAME="${RUNTIME_ACCESS_SECURITY_GROUP_NAME:-data-agent-runtime-endpoint-access}"

aws cloudformation deploy \
  --region "${REGION}" \
  --stack-name "${PRIVATE_SERVICE_ENDPOINT_STACK_NAME}" \
  --template-file "${ROOT}/infrastructure/private-endpoints.yaml" \
  --parameter-overrides \
    Environment="${ENVIRONMENT}" \
    CreatePrivateServiceEndpoints="${CREATE_PRIVATE_SERVICE_ENDPOINTS}" \
    VpcId="${VPC_ID}" \
    EndpointSubnetIds="${ENDPOINT_SUBNET_IDS}" \
    RuntimeSecurityGroupIds="${RUNTIME_SECURITY_GROUP_IDS}" \
    RuntimeRouteTableIds="${RUNTIME_ROUTE_TABLE_IDS}" \
    EndpointIngressCidr="${ENDPOINT_INGRESS_CIDR}" \
    EndpointSecurityGroupName="${ENDPOINT_SECURITY_GROUP_NAME}" \
    RuntimeAccessSecurityGroupName="${RUNTIME_ACCESS_SECURITY_GROUP_NAME}"

echo "PRIVATE_SERVICE_ENDPOINT_STACK_NAME=${PRIVATE_SERVICE_ENDPOINT_STACK_NAME}"
echo "RUNTIME_ACCESS_SECURITY_GROUP_ID=$(stack_output "${REGION}" "${PRIVATE_SERVICE_ENDPOINT_STACK_NAME}" RuntimeAccessSecurityGroupId)"
