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

Local validation:

```bash
python .tools/run_tests.py
python .tools/run_lint.py
python .tools/run_typecheck.py
python .tools/run_compile.py
git diff --check
```

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
