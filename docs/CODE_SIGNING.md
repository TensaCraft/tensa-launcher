# Code Signing Policy

TensaLauncher release artifacts are built by GitHub Actions from this repository and published through GitHub Releases.

## Windows Release Signing

The project is prepared for free Windows code signing through SignPath.io with a certificate provided by SignPath Foundation after the repository is public and accepted for the SignPath Foundation open-source program.

For SignPath-enabled release builds:

- Free code signing is provided by SignPath.io, certificate by SignPath Foundation.
- Signed artifacts must be built from this repository by the official GitHub Actions release workflow.
- Release signing is limited to tagged TensaLauncher release artifacts.
- Signing requests must be approved by a trusted project maintainer.
- Locally built artifacts are development builds and must not be represented as official signed releases.

## Team Roles

- Committers and reviewers: TensaCraft repository collaborators with write or maintain access.
- Approvers: TensaCraft organization owners and designated release maintainers.

## Privacy Policy

The launcher uses network services for Minecraft authentication, modpack metadata, updates, and user-initiated reports. See [PRIVACY.md](PRIVACY.md).

## Unsigned Builds

Until SignPath release signing is enabled, Windows artifacts may be unsigned. Unsigned builds are still produced from the public release workflow and attached to GitHub Releases.
