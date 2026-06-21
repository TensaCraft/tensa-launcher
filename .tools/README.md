# Build Tools

TensaLauncher is built and validated with Python 3.13. Local helper scripts prefer a Python 3.13 interpreter even if the system `python` command points to another runtime.

Install development dependencies first:

```bash
py -3.13 -m pip install -e .[dev]   # Windows
python3.13 -m pip install -e .[dev] # Linux/macOS
```

## Validation

```bash
python .tools/run_tests.py
python .tools/run_lint.py
python .tools/run_typecheck.py
python .tools/run_compile.py
git diff --check
```

## Build Commands

Linux:

```bash
python .tools/build.py --target linux
```

Outputs:

- `.build/linux/TensaLauncher`
- `.build/linux/TensaLauncher-x86_64.AppImage`

Linux raw binary:

```bash
python .tools/build.py --target linux --linux-format binary
```

macOS:

```bash
python .tools/build.py --target macos
```

Output:

- `.build/macos/TensaLauncher.dmg`

macOS without DMG:

```bash
python .tools/build.py --target macos --skip-dmg
```

Windows:

```bash
python .tools/build.py --target windows
```

Output:

- `.build/windows/TensaLauncher.exe`

Windows with installer:

```bash
python .tools/build.py --target windows --with-windows-installer
```

Output:

- `.build/windows/TensaLauncher.exe`
- `.build/windows/TensaLauncherInstaller.exe`

## Packaged Smoke Tests

The release workflow runs `.tools/smoke_packaged.py` against produced artifacts. For local Windows executable checks with Windows Sandbox:

```powershell
powershell -ExecutionPolicy Bypass -File .tools\run_exe_sandbox.ps1 -Build
```

## Release Metadata

Release metadata is derived from `launcher.__version__`:

```bash
python .tools/release_meta.py
python .tools/release_notes.py --tag HEAD --output RELEASE_NOTES.md
```

## Optional Authenticode Signing

Windows builds can be signed with a local or CI-provided PFX certificate by setting:

- `TENSALAUNCHER_WINDOWS_CERT_PATH`
- `TENSALAUNCHER_WINDOWS_CERT_PASSWORD`
- `TENSALAUNCHER_WINDOWS_TIMESTAMP_URL`

Legacy `TCL_*` environment variable names are still accepted for existing scripts.
