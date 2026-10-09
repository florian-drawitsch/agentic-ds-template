"""Unit tests for the Azure stack with Pulumi mocks: no cloud account, no costs."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any

import pulumi
import pytest
from stack import PLACEHOLDER_IMAGE, StackConfig, build


class Mocks(pulumi.runtime.Mocks):
    """Record every resource and answer the provider calls the stack makes."""

    def __init__(self) -> None:
        self.resources: list[pulumi.runtime.MockResourceArgs] = []

    def new_resource(self, args: pulumi.runtime.MockResourceArgs) -> tuple[str, dict[str, Any]]:
        self.resources.append(args)
        outputs = dict(args.inputs) | {
            "name": args.name,
            "principalId": "principal",
            "clientId": "client",
        }
        identity = outputs.get("identity")
        if isinstance(identity, dict) and isinstance(identity.get("userAssignedIdentities"), list):
            # Azure returns user-assigned identities as a map from resource id to details.
            ids = identity["userAssignedIdentities"]
            outputs["identity"] = identity | {"userAssignedIdentities": {i: {} for i in ids}}
        if args.typ.endswith("containerregistry:Registry"):
            outputs["loginServer"] = f"{args.name}.azurecr.io"
        if args.typ.endswith("keyvault:Vault"):
            outputs["properties"] = args.inputs["properties"] | {
                "vaultUri": "https://kv.vault.azure.net/"
            }
        if args.typ.endswith("machinelearningservices:Workspace"):
            outputs["mlFlowTrackingUri"] = "azureml://tracking"
        if args.typ.endswith("app:ContainerApp"):
            configuration = args.inputs["configuration"]
            outputs["configuration"] = configuration | {
                "ingress": configuration["ingress"] | {"fqdn": "api.example.io"}
            }
        return f"{args.name}-id", outputs

    def call(self, args: pulumi.runtime.MockCallArgs) -> dict[str, Any]:
        if args.token.endswith("getClientConfig"):
            return {
                "tenantId": "tenant",
                "subscriptionId": "subscription",
                "clientId": "c",
                "objectId": "o",
            }
        if args.token.endswith("getSharedKeys"):
            return {"primarySharedKey": "key"}
        if args.token.endswith("listStorageAccountKeys"):
            return {"keys": [{"keyName": "key1", "value": "secret", "permissions": "Full"}]}
        return {}


@pytest.fixture
def mocks() -> Iterator[Mocks]:
    # Python 3.14 no longer creates an event loop implicitly; Pulumi's test runtime needs one.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    recorder = Mocks()
    pulumi.runtime.set_mocks(recorder, preview=False)
    yield recorder
    loop.close()
    asyncio.set_event_loop(None)


def types(mocks: Mocks) -> set[str]:
    """Return the resource types as `module:Type`, for example `app:Job`."""
    return {":".join(r.typ.split(":")[-2:]) for r in mocks.resources}


def first(mocks: Mocks, suffix: str) -> pulumi.runtime.MockResourceArgs:
    return next(r for r in mocks.resources if r.typ.endswith(suffix))


ALL = StackConfig(stack="test", job=True, api=True, storage=True, key_vault=True, mlflow=True)
NONE = StackConfig(stack="test", job=False, api=False, storage=False, key_vault=False, mlflow=False)


@pulumi.runtime.test
def test_every_component(mocks: Mocks) -> pulumi.Output:
    outputs = build(ALL)

    def check(values: list[Any]) -> None:
        assert {
            "app:Job",
            "app:ContainerApp",
            "app:ManagedEnvironmentsStorage",
            "keyvault:Vault",
            "machinelearningservices:Workspace",
            "storage:FileShare",
        } <= types(mocks)
        assert dict(zip(outputs, values, strict=True))["api_url"] == "https://api.example.io"

    return pulumi.Output.all(*outputs.values()).apply(check)


@pulumi.runtime.test
def test_core_only(mocks: Mocks) -> pulumi.Output:
    outputs = build(NONE)

    def check(_: list[Any]) -> None:
        assert {
            "resources:ResourceGroup",
            "containerregistry:Registry",
            "managedidentity:UserAssignedIdentity",
            "app:ManagedEnvironment",
        } <= types(mocks)
        assert not {
            "app:Job",
            "app:ContainerApp",
            "keyvault:Vault",
            "storage:StorageAccount",
            "machinelearningservices:Workspace",
        } & types(mocks)

    return pulumi.Output.all(*outputs.values()).apply(check)


@pulumi.runtime.test
def test_secure_defaults(mocks: Mocks) -> pulumi.Output:
    outputs = build(ALL)

    def check(_: list[Any]) -> None:
        assert first(mocks, "containerregistry:Registry").inputs["adminUserEnabled"] is False
        account = first(mocks, "storage:StorageAccount").inputs
        assert account["allowBlobPublicAccess"] is False
        assert account["minimumTlsVersion"] == "TLS1_2"
        assert (
            first(mocks, "keyvault:Vault").inputs["properties"]["enableRbacAuthorization"] is True
        )

    return pulumi.Output.all(*outputs.values()).apply(check)


@pulumi.runtime.test
def test_job_mounts_the_data_share_and_uses_the_placeholder_until_release(
    mocks: Mocks,
) -> pulumi.Output:
    outputs = build(ALL)

    def check(_: list[Any]) -> None:
        container = first(mocks, "app:Job").inputs["template"]["containers"][0]
        assert container["image"] == PLACEHOLDER_IMAGE
        assert container["volumeMounts"] == [{"volumeName": "data", "mountPath": "/app/data"}]

    return pulumi.Output.all(*outputs.values()).apply(check)


@pulumi.runtime.test
def test_released_image_comes_from_the_registry(mocks: Mocks) -> pulumi.Output:
    outputs = build(StackConfig(stack="test", image_tag="abc123", job=True, api=True))

    def check(_: list[Any]) -> None:
        image = first(mocks, "app:ContainerApp").inputs["template"]["containers"][0]["image"]
        assert ".azurecr.io/" in image
        assert image.endswith(":abc123")
        assert (
            first(mocks, "app:ContainerApp").inputs["configuration"]["ingress"]["targetPort"]
            == 8000
        )

    return pulumi.Output.all(*outputs.values()).apply(check)
