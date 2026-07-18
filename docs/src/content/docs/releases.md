---
title: Releases
description: How Lyra versions application and Python package releases.
---

Lyra uses Release Please to prepare semantic releases from Conventional
Commits merged into `main`. Development documentation and the `dev` container
continue to deploy from `dev`; they are not separate semantic release units.

## Release Components

The repository has six independently versioned components:

| Component | Source path | Tag example |
| --- | --- | --- |
| Application | repository root and `lyra_app/` | `lyra-app-v0.1.0` |
| SDK | `packages/lyra_sdk/` | `lyra-sdk-v0.1.0` |
| API client | `packages/lyra_api/` | `lyra-api-v0.1.0` |
| Utilities | `packages/lyra_utils/` | `lyra-utils-v0.1.0` |
| MCP server | `packages/lyra_mcp/` | `lyra-mcp-v0.1.0` |
| TUI | `packages/lyra_tui/` | `lyra-tui-v0.1.0` |

The application container embeds the SDK, utilities, and MCP packages.
Changes to those package paths can therefore release both the changed package
and `lyra-app`. Changes limited to documentation, `lyra-api`, or `lyra-tui` do
not release the application image.

## Release Flow

1. Merge Conventional Commits into `main`, preferably by squash-merging a PR
   with a Conventional Commit title.
2. The Release Please workflow creates or updates one combined release PR for
   every affected component.
3. The release lockfile workflow runs `uv lock` on that PR and commits any
   workspace-version changes to `uv.lock`.
4. CI validates the release PR like any other change.
5. Merge the release PR to create component tags and GitHub Releases.
6. Each released Python tag is installed and imported in a fresh uv project.
7. When `lyra-app` is among the released components, the same workflow builds
   the versioned Docker image and updates the `latest` image tag.

Release Please interprets `fix` commits as patch changes and `feat` commits as
minor changes. An exclamation mark or `BREAKING CHANGE` footer marks a breaking
change. While a component remains below `1.0.0`, breaking changes advance its
minor version rather than immediately creating `1.0.0`.

## GitHub Configuration

The workflows require a repository secret named `RELEASE_PLEASE_TOKEN`. Use a
fine-grained personal access token or GitHub App token that can write repository
contents, pull requests, and issues. A token other than the workflow's built-in
`GITHUB_TOKEN` is required so that CI and lockfile workflows run for the
generated release PR.

In the repository Actions settings, allow GitHub Actions to create pull
requests. Protect `main` by requiring the CI workflow before merge.

## Installing Tagged Python Packages

Consumers install a component tag and package subdirectory with `uv`. For
example:

```bash
uv add "git+https://github.com/RodolfoFigueroa/lyra@lyra-api-v0.1.0#subdirectory=packages/lyra_api"
```

The uv workspace sources resolve sibling Lyra dependencies from the same Git
commit. The resulting consumer lockfile records that commit for reproducible
installs.

## Initial Release

The first run uses the configured bootstrap commit and creates `0.1.0` releases
from the existing source history. After the first release PR is generated, the
bootstrap setting is ignored and can be removed from
`release-please-config.json`.
