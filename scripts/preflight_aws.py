import argparse
import json

import boto3
import yaml


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parameters", required=True)
    parser.add_argument("--config", required=True)
    args = parser.parse_args()

    with open(args.parameters, encoding="utf-8") as handle:
        parameters = json.load(handle)
    with open(args.config, encoding="utf-8") as handle:
        config = yaml.safe_load(handle)

    region = parameters["region"]
    ec2 = boto3.client("ec2", region_name=region)
    subnet_ids = parameters["private_subnet_ids"].split(",")
    security_group_ids = parameters["runtime_security_group_ids"].split(",")

    subnets = ec2.describe_subnets(SubnetIds=subnet_ids)["Subnets"]
    if len(subnets) != len(subnet_ids):
        raise SystemExit("Not all configured subnets exist")
    vpc_ids = {subnet["VpcId"] for subnet in subnets}
    if len(vpc_ids) != 1:
        raise SystemExit("All Runtime subnets must belong to the same VPC")

    groups = ec2.describe_security_groups(GroupIds=security_group_ids)["SecurityGroups"]
    if any(group["VpcId"] not in vpc_ids for group in groups):
        raise SystemExit("Runtime security groups must belong to the subnet VPC")

    boto3.client("secretsmanager", region_name=region).describe_secret(
        SecretId=parameters["database_secret_arn"]
    )

    if config["llm"]["provider"] == "bedrock":
        model = config["llm"].get("bedrock_model_id") or config["llm"]["model"]
        boto3.client("bedrock", region_name=region).get_inference_profile(
            inferenceProfileIdentifier=model
        )

    print(
        "AWS preflight passed for configured networking, database secret, and model. "
        "Service endpoint and database connectivity still require a Runtime smoke test."
    )


if __name__ == "__main__":
    main()
