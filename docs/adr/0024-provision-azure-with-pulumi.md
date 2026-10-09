# ADR-0024: Provision Azure with Pulumi in Python

- **Status:** Accepted
- **Date:** 2026-10-09

## Context

Infrastructure as code was out of scope for version 1. Several past projects deployed to Azure,
all with Pulumi. A deployment-ready template needs a tested path from repository to cloud.

## Decision

The `infrastructure` question offers `none` (default) or `azure`. With `azure`, a separate uv
project in `infra/` holds a Pulumi program in Python.

**Always created:**
- a resource group
- a container registry without admin user
- a user-assigned managed identity with pull rights
- Log Analytics
- a Container Apps environment

**Optional components** (`azure_components`), switchable per stack later in
`Pulumi.<stack>.yaml`:
- a scheduled job that runs the pipeline image
- a container app for the API
- an Azure Files share mounted as `/app/data`, so the code runs unchanged
- Key Vault with RBAC
- an Azure Machine Learning workspace as the managed MLflow tracking server

**State and delivery:**
- State lives in Azure Blob Storage (`task infra:bootstrap`), with passphrase-encrypted secrets.
- `task infra:release` builds the image with `az acr build` and rolls it out. Until the first
  release, a public placeholder image runs.
- The deploy workflow runs only on demand and signs in with OpenID Connect.

## Alternatives considered

- **OpenTofu or Terraform:** more common, but a second language. Pulumi in Python keeps one
  toolchain (uv, pytest, ruff) and matches past projects.
- **Several clouds:** each cloud roughly doubles the infrastructure code and its tests. Azure
  first.
- **MLflow as a self-hosted container:** needs a database and authentication. The Azure ML
  workspace provides a managed tracking server with Entra ID access.

## Consequences

- Choosing Azure forces the Dockerfile.
- The deploying principal needs Contributor and Role Based Access Control Administrator, because
  the stack assigns roles.
- The API endpoint is public without authentication. The deployment guide says to enable
  Container Apps authentication before serving confidential data.
- Python 3.14 needs an explicit event loop in Pulumi's mock tests. The generated tests create
  one.

## Verification

- `task infra:test` runs Pulumi mock tests for:
  - all components and the core-only stack
  - secure defaults: no registry admin, no public blob access, RBAC Key Vault
  - the data mount, the placeholder image and released images
- Render tests check files, defaults and validation.
- End-to-end tests run the generated gate, including the infrastructure tests.
- A real deployment has not been tested in this repository's CI.
