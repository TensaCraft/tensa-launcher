# Build Tools

The launcher is built and validated with Python 3.13. Local helper scripts will
prefer a Python 3.13 interpreter even if the system `python` command points to a
newer preview/runtime.

For local development, prefer installing the project in editable mode first:

```bash
py -3.13 -m pip install -e .[dev]   # Windows
python3.13 -m pip install -e .[dev] # Linux/macOS
```

Recommended entrypoints use the short `tl` CLI locally:

```bash
tl clean
tl build --target linux
tl build --target macos
tl build --target windows
```

Direct build script usage is still available:

```bash
python .tools/build.py --target linux
python .tools/build.py --target macos
python .tools/build.py --target windows
```

Common rules:

- launcher pack entrypoint is `launcher/main.py`;
- packaged assets come from `launcher/assets/`;
- final artifacts are copied to `.build/<target>/` (overwrite enabled; generated files are removed);
- temporary `dist/` and `build/` workdirs are removed after success;
- build prerequisites are installed from `pyproject.toml` runtime dependencies plus the `build` extra, without reinstalling the local package;
- `tl clean` removes generated caches and legacy root runtime leftovers;
- use `--no-cleanup` to keep temporary files for debugging.

## Launcher Icons

`launcher/assets/logo.png` is the shared artwork source. Keep it high resolution
(at least 1024 pixels), transparent, and tightly framed. The sidebar displays this PNG directly.

After replacing the source, regenerate the bundled native icons:

```bash
python .tools/icon_assets.py
```

This updates `launcher/assets/logo.ico` (Windows window icon) and `launcher/assets/icon.icns`
(macOS native asset). Builds also generate platform icons directly from `logo.png` through the
same helper: a multi-resolution Windows ICO (16-256 pixels), macOS ICNS (up to 1024 pixels),
and a transparent 512-pixel Linux PNG. The generator fits the artwork without adding an inset,
stretching, cropping, or a colored background. Non-square sources are centered on a square canvas
with their aspect ratio preserved. Regression tests compare bundled icons with the source.

Windows EXE and installer resources use the generated ICO. Installed shortcuts and the
uninstall entry use the EXE's embedded icon, so replacing the EXE does not leave them pointing
at a stale sidecar icon. Reinstall to refresh shortcuts created by an older installer; pinned
shortcuts and the desktop shell may retain cached artwork until refreshed.

The macOS app bundle uses ICNS; Linux AppImage uses the PNG for its desktop entry and `.DirIcon`.
Native app/taskbar rendering must be verified on each platform with its packaged build, not just
by running the Python source. Rebuild existing packages after changing the artwork.

## Validation

```bash
python .tools/run_tests.py
python .tools/run_lint.py
python .tools/run_typecheck.py
python .tools/run_compile.py
git diff --check
```

Repeatable content scan benchmark (uses temporary fixtures, not player data):

```bash
python .tools/benchmark_content.py --mods 200
```

This reports median times for cold/repeated local JAR metadata scans and installed
component discovery. It excludes downloads, content hash verification, and UI
rendering; these numbers are not total launcher startup times.

Git hooks:

```bash
git config core.hooksPath .githooks
```

The `pre-commit` hook runs CodeQL CLI detection, lint, type checks, compile,
tests, and staged whitespace checks before a commit is created. Set
`TENSALAUNCHER_PRECOMMIT_CODEQL=1` to include a full local CodeQL analysis in
the hook as well.

CodeQL:

```bash
python .tools/run_codeql.py
python .tools/run_codeql.py --analyze
```

The full analysis uses the `python-security-and-quality` CodeQL suite and
writes SARIF to `.codeql/results/python-security-and-quality.sarif`.

On Windows the helper automatically checks the standard user install path:
`%LOCALAPPDATA%\Programs\CodeQL\codeql\codeql.exe`. Use
`TENSALAUNCHER_CODEQL_BIN` if CodeQL is installed elsewhere.

Linux output (default):

```bash
python .tools/build.py --target linux
```

- `.build/linux/TensaLauncher`
- `.build/linux/TensaLauncher-x86_64.AppImage`

Linux raw binary (optional):

```bash
python .tools/build.py --target linux --linux-format binary
```

- `.build/linux/TensaLauncher`

macOS output:

```bash
python .tools/build.py --target macos
```

- `.build/macos/TensaLauncher.dmg`

macOS without DMG (optional):

```bash
python .tools/build.py --target macos --skip-dmg
```

- `.build/macos/TensaLauncher.app`

Windows installer:

```bash
python .tools/build.py --target windows --with-windows-installer
```

Output:

- `.build/windows/TensaLauncher.exe`
- `.build/windows/TensaLauncherInstaller.exe`

Windows Sandbox smoke test for the unpackaged executable:

```powershell
powershell -ExecutionPolicy Bypass -File .tools\run_exe_sandbox.ps1 -Build
```

The helper prepares `.build/sandbox-exe/TensaLauncher`, generates a
`TensaLauncher.wsb` file, maps the executable into Windows Sandbox, copies it to
the sandbox desktop, and starts `TensaLauncher.exe`. Use `-NoLaunch` to only
prepare the sandbox payload.

If Python 3.13 is installed but not first on `PATH`, `build.py` will still
resolve it automatically through the Windows `py -3.13` launcher or
`python3.13`. You can override the interpreter explicitly with
`--python-bin` or `TENSALAUNCHER_PYTHON_BIN`.

Optional signing environment variables:

- `TENSALAUNCHER_WINDOWS_CERT_PATH`, `TENSALAUNCHER_WINDOWS_CERT_PASSWORD`, `TENSALAUNCHER_WINDOWS_TIMESTAMP_URL` for Windows EXE and installer signing.

Legacy `TCL_*` environment variable names are still accepted for existing local scripts.
