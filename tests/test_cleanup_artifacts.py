from scripts.cleanup_artifacts import _delete_keys, _stack_parameter_keys


class FakeS3Client:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    def delete_object(self, Bucket: str, Key: str) -> None:
        self.deleted.append(f"{Bucket}/{Key}")


class FakeCloudFormationClient:
    class exceptions:
        ClientError = RuntimeError

    def describe_stacks(self, StackName: str) -> dict:
        return {
            "Stacks": [
                {
                    "Parameters": [
                        {
                            "ParameterKey": "ArtifactKey",
                            "ParameterValue": "artifacts/prod/current.zip",
                        },
                        {
                            "ParameterKey": "ConfigKey",
                            "ParameterValue": "config/prod/current.yaml",
                        },
                    ]
                }
            ]
        }


def test_cleanup_dry_run_does_not_delete(capsys) -> None:
    client = FakeS3Client()

    _delete_keys(client, "bucket", ["artifacts/prod/old.zip"], apply=False)

    assert client.deleted == []
    assert "Would delete s3://bucket/artifacts/prod/old.zip" in capsys.readouterr().out


def test_cleanup_apply_deletes_key() -> None:
    client = FakeS3Client()

    _delete_keys(client, "bucket", ["artifacts/prod/old.zip"], apply=True)

    assert client.deleted == ["bucket/artifacts/prod/old.zip"]


def test_reads_runtime_stack_artifact_and_config_keys() -> None:
    artifact_key, config_key = _stack_parameter_keys(
        FakeCloudFormationClient(), "data-agent-runtime-prod"
    )

    assert artifact_key == "artifacts/prod/current.zip"
    assert config_key == "config/prod/current.yaml"
