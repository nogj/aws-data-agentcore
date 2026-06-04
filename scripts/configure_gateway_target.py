"""Validate and configure the Bedrock AgentCore Gateway target.

This script ensures the gateway target CloudFormation template is properly
configured before deployment. It validates the template structure, parameters,
and runtime connectivity prerequisites.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import boto3
import yaml


def _validate_template_structure(template: dict[str, Any]) -> None:
    """Validate that the template has required CloudFormation structure."""
    if "AWSTemplateFormatVersion" not in template:
        raise SystemExit("Template missing AWSTemplateFormatVersion")
    if "Resources" not in template:
        raise SystemExit("Template missing Resources section")
    if "GatewayTarget" not in template["Resources"]:
        raise SystemExit("Template missing GatewayTarget resource")

    gateway_target = template["Resources"]["GatewayTarget"]
    if gateway_target.get("Type") != "AWS::BedrockAgentCore::GatewayTarget":
        raise SystemExit("GatewayTarget resource has incorrect Type")

    properties = gateway_target.get("Properties", {})
    if "GatewayIdentifier" not in properties:
        raise SystemExit("GatewayTarget missing GatewayIdentifier property")
    if "TargetConfiguration" not in properties:
        raise SystemExit("GatewayTarget missing TargetConfiguration property")


def _validate_gateway_exists(region: str, gateway_id: str) -> None:
    """Verify that the specified gateway exists in AWS."""
    client = boto3.client("bedrock-agentcore", region_name=region)
    try:
        client.get_gateway(gatewayIdentifier=gateway_id)
    except client.exceptions.ResourceNotFoundException:
        raise SystemExit(f"Gateway {gateway_id} not found in region {region}")
    except Exception as e:
        raise SystemExit(f"Failed to verify gateway: {e}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate and configure the Bedrock AgentCore Gateway target."
    )
    parser.add_argument("--template", required=True, help="Path to target.yaml")
    parser.add_argument(
        "--region", required=True, help="AWS region for the gateway"
    )
    parser.add_argument(
        "--gateway-id", required=True, help="Bedrock AgentCore Gateway ID"
    )
    parser.add_argument(
        "--skip-aws-validation",
        action="store_true",
        help="Skip AWS connectivity checks",
    )
    args = parser.parse_args()

    template_path = Path(args.template)
    if not template_path.exists():
        raise SystemExit(f"Template file not found: {args.template}")

    with template_path.open(encoding="utf-8") as handle:
        try:
            template = yaml.safe_load(handle)
        except yaml.YAMLError as e:
            raise SystemExit(f"Invalid YAML template: {e}")

    # Validate template structure
    _validate_template_structure(template)

    # Validate AWS gateway exists if not skipped
    if not args.skip_aws_validation:
        _validate_gateway_exists(args.region, args.gateway_id)

    print("✓ Gateway target configuration is valid")


if __name__ == "__main__":
    main()
