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

## GitHub Actions Integration

The Windows release workflow is wired for automatic SignPath signing. When SignPath credentials are configured, every Windows `.exe` built by `.github/workflows/build.yml` is uploaded to SignPath, signed, copied back into `.build/windows/`, smoke-tested, and then published as the GitHub Release asset.

Required GitHub repository secret:

- `SIGNPATH_API_TOKEN`

Required GitHub repository variables:

- `SIGNPATH_ORGANIZATION_ID`
- `SIGNPATH_PROJECT_SLUG`
- `SIGNPATH_SIGNING_POLICY_SLUG`
- `SIGNPATH_ARTIFACT_CONFIGURATION_SLUG`

Recommended SignPath values:

- project slug: `tensa-launcher`
- signing policy slug: `release-signing`
- artifact configuration slug: `windows-exes`

The SignPath artifact configuration should treat the GitHub artifact as a ZIP file and sign every `.exe` at the artifact root. The current Windows build can include:

- `TensaLauncher.exe`
- `TensaLauncherInstaller.exe`

The source/build policy is stored in `.signpath/policies/tensa-launcher/release-signing.yml`.

## Team Roles

- Committers and reviewers: TensaCraft repository collaborators with write or maintain access.
- Approvers: TensaCraft organization owners and designated release maintainers.

## Privacy Policy

The launcher uses network services for Minecraft authentication, modpack metadata, updates, and user-initiated reports. See [PRIVACY.md](PRIVACY.md).

## Unsigned Builds

Until the SignPath Foundation project is approved and the required GitHub secret/variables are configured, Windows artifacts are produced unsigned. After configuration, the same release workflow automatically publishes the signed `.exe` files.
