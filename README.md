# TensaLauncher

TensaLauncher is a cross-platform desktop launcher for Minecraft modpacks, built with Python 3.13 and Flet.

It manages Minecraft versions, Java runtimes, modpack installs, component repair, launcher updates, and user feedback/reporting workflows for TensaCraft players.

## Features

- Minecraft profile management and Microsoft authentication.
- TensaCraft modpack catalog, install, sync, and repair workflows.
- Modrinth and CurseForge import support.
- Minecraft install and launch integration powered by `minecraft-launcher-lib`.
- Java runtime discovery and per-version runtime preferences.
- Local backup and restore tools for Minecraft worlds.
- Cross-platform release builds for Windows, Linux, and macOS.

## Requirements

- Python `3.13`
- Windows, Linux, or macOS

## Development Setup

Install the project with development dependencies:

```bash
python -m pip install -e .[dev]
```

Run the launcher:

```bash
tl run
```

If the `tl` console script is not installed, use:

```bash
python -m launcher.cli run
```

## Validation

Run the same checks used by CI:

```bash
python .tools/run_tests.py
python .tools/run_lint.py
python .tools/run_typecheck.py
python .tools/run_compile.py
git diff --check
```

## Build

Windows:

```bash
python .tools/build.py --target windows
```

Windows with installer:

```bash
python .tools/build.py --target windows --with-windows-installer
```

Linux:

```bash
python .tools/build.py --target linux
```

macOS:

```bash
python .tools/build.py --target macos
```

Build outputs are written to `.build/<target>/`.

## Release Flow

The launcher version is defined in `launcher/__init__.py`.

The release workflow derives:

- tag: `vX.Y.Z`
- title: `Release vX.Y.Z`
- release notes: generated from Conventional Commit subjects

Official release artifacts are built by GitHub Actions from this repository and attached to GitHub Releases.

## Code Signing Policy

Free code signing provided by SignPath.io, certificate by SignPath Foundation, after the project is accepted by SignPath Foundation.

See [docs/CODE_SIGNING.md](docs/CODE_SIGNING.md) for the project policy and release-signing rules.

## Privacy

See [docs/PRIVACY.md](docs/PRIVACY.md) for network services used by the launcher and the data they may receive.

## Security

Please report vulnerabilities privately. See [SECURITY.md](SECURITY.md).

## License

TensaLauncher is released under the [MIT License](LICENSE).
