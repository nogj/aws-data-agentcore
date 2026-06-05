import argparse
import json
from collections.abc import Iterable

import boto3


def _stack_parameter_keys(client, stack_name: str) -> tuple[str | None, str | None]:
    """Read artifact and config keys currently used by a deployed Runtime stack."""

    try:
        stack = client.describe_stacks(StackName=stack_name)["Stacks"][0]
    except client.exceptions.ClientError as exc:
        if "does not exist" in str(exc):
            return None, None
        raise

    parameters = {
        parameter["ParameterKey"]: parameter["ParameterValue"]
        for parameter in stack.get("Parameters", [])
    }
    return parameters.get("ArtifactKey"), parameters.get("ConfigKey")


def _list_keys(client, bucket: str, prefix: str) -> set[str]:
    keys: set[str] = set()
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        keys.update(item["Key"] for item in page.get("Contents", []))
    return keys


def _load_json(client, bucket: str, key: str) -> dict:
    body = client.get_object(Bucket=bucket, Key=key)["Body"].read()
    return json.loads(body)


def _delete_keys(client, bucket: str, keys: Iterable[str], apply: bool) -> None:
    for key in sorted(keys):
        if apply:
            client.delete_object(Bucket=bucket, Key=key)
            print(f"Deleted s3://{bucket}/{key}")
        else:
            print(f"Would delete s3://{bucket}/{key}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--environment", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--runtime-stack")
    parser.add_argument("--instance", default="data-agent")
    parser.add_argument("--keep-manifests", type=int, default=10)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    s3 = boto3.client("s3")
    cloudformation = boto3.client("cloudformation", region_name=args.region)
    stack_suffix = "" if args.instance == "data-agent" else f"-{args.instance}"
    runtime_stack = args.runtime_stack or f"data-agent-runtime-{args.environment}{stack_suffix}"
    if args.instance == "data-agent":
        manifest_prefix = f"manifests/{args.environment}/data-agent-"
        active_key = f"manifests/{args.environment}/active.json"
        artifact_prefix = f"artifacts/{args.environment}/data-agent-"
        config_prefix = f"config/{args.environment}/data-agent-"
    else:
        manifest_prefix = f"manifests/{args.environment}/{args.instance}/data-agent-"
        active_key = f"manifests/{args.environment}/{args.instance}/active.json"
        artifact_prefix = f"artifacts/{args.environment}/{args.instance}/"
        config_prefix = f"config/{args.environment}/{args.instance}/"
    manifest_keys = sorted(_list_keys(s3, args.bucket, manifest_prefix), reverse=True)
    active_manifest = _load_json(s3, args.bucket, active_key)

    kept_manifest_keys = set(manifest_keys[: args.keep_manifests])
    kept_artifacts = {active_manifest["artifact_key"]}
    kept_configs = {active_manifest["config_key"]}
    runtime_artifact, runtime_config = _stack_parameter_keys(cloudformation, runtime_stack)
    if runtime_artifact:
        kept_artifacts.add(runtime_artifact)
    if runtime_config:
        kept_configs.add(runtime_config)
    for key in kept_manifest_keys:
        manifest = _load_json(s3, args.bucket, key)
        kept_artifacts.add(manifest["artifact_key"])
        kept_configs.add(manifest["config_key"])

    all_artifacts = _list_keys(s3, args.bucket, artifact_prefix)
    all_configs = _list_keys(s3, args.bucket, config_prefix)
    removable_manifests = set(manifest_keys[args.keep_manifests :])
    removable = (
        (all_artifacts - kept_artifacts)
        | (all_configs - kept_configs)
        | removable_manifests
    )
    _delete_keys(s3, args.bucket, removable, args.apply)


if __name__ == "__main__":
    main()
