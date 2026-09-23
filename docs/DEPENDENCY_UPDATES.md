# Dependency Updates

Audited 2026-09-23 on Windows x64, CPython 3.13.9, pip 26.2.1.
Checked every direct runtime, build-extra, dev-extra and build-backend requirement
using the structured PyPI JSON API: discard prereleases/dev releases and yanked
files, check `Requires-Python`, then resolve the complete dependency set with pip.
Versions below are the newest compatible stable releases available on that date.

## Pins

| Dependency (PyPI evidence) | Group | Previous | Selected |
| --- | --- | --- | --- |
| [cryptography](https://pypi.org/pypi/cryptography/json) | runtime | 50.0.1 | unchanged |
| [flet](https://pypi.org/pypi/flet/json) | runtime | 0.86.5 | **1.0.1** |
| [flet-desktop](https://pypi.org/pypi/flet-desktop/json) | runtime | 0.86.5 | **1.0.1** |
| [minecraft-launcher-lib](https://pypi.org/pypi/minecraft-launcher-lib/json) | runtime | 8.0 | unchanged |
| [packaging](https://pypi.org/pypi/packaging/json) | runtime | 26.3 | unchanged |
| [pyobjc-framework-AVFoundation](https://pypi.org/pypi/pyobjc-framework-AVFoundation/json) | runtime, macOS only | 12.2.2 | unchanged |
| [requests](https://pypi.org/pypi/requests/json) | runtime | 2.34.2 | unchanged |
| [transliterate](https://pypi.org/pypi/transliterate/json) | runtime | 1.10.2 | unchanged |
| [flet-cli](https://pypi.org/pypi/flet-cli/json) | build | 0.86.5 | **1.0.1** |
| [pillow](https://pypi.org/pypi/pillow/json) | runtime, build, dev | 12.3.0 | unchanged pin; runtime added |
| [pyinstaller](https://pypi.org/pypi/pyinstaller/json) | build | 6.22.2 | **6.22.3** |
| [pyright](https://pypi.org/pypi/pyright/json) | dev | 1.1.411 | **1.1.414** |
| [pytest](https://pypi.org/pypi/pytest/json) | dev | 9.1.1 | unchanged |
| [ruff](https://pypi.org/pypi/ruff/json) | dev | 0.16.6 | **0.16.8** |
| [setuptools](https://pypi.org/pypi/setuptools/json) | backend | >=80 | range retained; 84.0.0 installed |
| [wheel](https://pypi.org/pypi/wheel/json) | backend | unbounded | policy retained; 0.48.0 installed |

The existing backend constraints already permit the latest releases; no backend
policy change was necessary. All active direct requirements match the installed
environment. Flet CLI additionally installs `flet-platform-assets==1.0.1`.
Existing compatible transitive packages were retained (pip's default
`only-if-needed` strategy), not independently upgraded. Python 3.12 `.venv` and
default Python 3.14 were not modified. Editable metadata was refreshed from the
existing application version; no application version file was changed.

Pillow is now an unconditional runtime dependency:
`launcher/platform/instance_shortcuts.py` imports `PIL.Image` and `ImageOps` to
convert per-instance shortcut icons to ICO/PNG/ICNS. Runtime-only installs must
not rely on build/dev extras. The existing `12.3.0` pins remain matched in all
three groups; a regression test checks the explicit runtime requirement, version
equality and absence of a platform marker. This changes dependency metadata only,
not the shortcut implementation or artwork.

## API Review

- [Flet 1.0.0](https://github.com/flet-dev/flet/releases/tag/v1.0.0) removes deprecated `app`, `Page.go`, `Page.launch_url`, page service accessors, `ElevatedButton`, `ConstrainedControl`, and `run(target=...)`. The audited baseline uses supported alternatives. `InputBorder` is now a class hierarchy; text-field/dropdown border properties remain compatible but require migration before 1.3.0. Native `flet build` shutdown no longer guarantees Python finalizers; persist state before exit. This project uses `flet pack`, not embedded `flet build`.
- [Flet 1.0.1](https://github.com/flet-dev/flet/releases/tag/v1.0.1) changes service registration to happen after `init()`: use `ft.context.page` during initialization, not `self.page`/`self.update()`. No custom Flet service `init()` overrides were found. Reviewed file-picker/service wiring without editing it. Installed CLI still accepts the build runner's `pack` flags; `get_artifact_filename()` and `get_package_bin_dir()` remain available. Desktop release assets were confirmed through GitHub's release API.
- [minecraft-launcher-lib 8.0 changelog](https://minecraft-launcher-lib.readthedocs.io/en/stable/changelog.html) adds the unified [mod_loader API](https://minecraft-launcher-lib.readthedocs.io/en/stable/modules/mod_loader.html) and deprecates separate Forge/Fabric/Quilt modules. Existing launcher code already uses `mod_loader`. Calls into private `_helper` internals remain an upstream compatibility risk; no newer stable release or required migration was found.
- [PyInstaller 6.22.3](https://pyinstaller.org/en/stable/CHANGES.html) fixes Windows RAMDISK behavior, icon suffix handling, and nested onefile subprocess validation. Packaged startup remains a native-test requirement.
- Pyright [1.1.412](https://github.com/microsoft/pyright/releases/tag/1.1.412), [1.1.413](https://github.com/microsoft/pyright/releases/tag/1.1.413), and [1.1.414](https://github.com/microsoft/pyright/releases/tag/1.1.414) change type inference/narrowing and dataclass handling. Ruff [0.16.7](https://github.com/astral-sh/ruff/releases/tag/0.16.7) and [0.16.8](https://github.com/astral-sh/ruff/releases/tag/0.16.8) include diagnostic/rule fixes. No lint-rule or typecheck-configuration changes were required for the isolated baseline.

## Packaging Fix

Fixed in `.tools/build.py`: it previously explicitly added the stock client
archive to `flet_desktop/app`, while the
[Flet hook](https://github.com/flet-dev/flet/blob/v1.0.1/sdk/python/packages/flet-cli/src/flet_cli/__pyinstaller/hook-flet.py)
adds the icon/metadata-patched archive to the same destination. PyInstaller
initializes data entries from command-line inputs before appending hook data;
[TOC normalization](https://github.com/pyinstaller/pyinstaller/blob/v6.22.3/PyInstaller/building/datastruct.py)
keeps the first same-type destination. An installed-library reproduction with
stock then patched `DATA` entries retained stock, confirming the collision.
Flet's patched `.sha256` sidecar can also survive beside the wrong archive.

Removed that explicit stock injection. `flet pack` now exclusively supplies the
patched archive and matching fingerprint through its hook; the self-contained
offline runtime is retained. No artwork changes or runtime/cache bypasses were
introduced. The normal build may download the upstream client once when uncached.
Regression tests cover all three platform commands and exercise the installed
Flet hook plus PyInstaller's actual TOC normalization with competing stock/patched
archives. All four regressions failed before the fix and passed afterward.
The hook-level test requires the build extra; command-level tests do not.

A real unsigned Windows onefile EXE was built from an isolated source snapshot
(`04678973124b6cc433e1863ebc740a6480d94b9b` plus dependency/build changes), without
touching the shared `build/` or `dist/` directories:

```powershell
# Working directory: .build/flet-pack-audit-20260923/source
py -3.13 .tools/build.py --target windows --skip-install --no-cleanup
# Working directory: repository root
py -3.13 .tools/smoke_packaged.py --target windows --artifact .build/flet-pack-audit-20260923/source/.build/windows/TensaLauncher.exe --timeout 60
```

Archive-level verification with `CArchiveReader`, `zipfile` and `pefile` passed:
- EXE size: 77,089,474 bytes; embedded `flet-windows.zip`: 41,700,390 bytes, 64 files, valid ZIP checksums and Flutter runtime DLL present.
- Archive SHA-256: `49c3a0713cefdb8e548ea4e6d86a59b29973d6024bd8dfd88485a516f1ea6acc`; bundled sidecar hash and byte count match.
- All 10 icon frames in both the outer EXE and embedded `flet/flet.exe` match the existing generated launcher ICO byte-for-byte. Inner client metadata identifies TensaLauncher/Tensa and the unchanged application version.
- The actual embedded archive successfully bootstrapped through `flet_desktop.ensure_client_cached()` with an empty test home and the download function replaced with a failing test stub. Only the package-directory/home lookups were redirected in this verification process; production cache behavior is unchanged. Extracted client bytes match the bundled client.
- Packaged `--smoke-test` passed. Verification files remain under `.build/flet-pack-audit-20260923/verification`.

## Validation

Commands used Python 3.13 explicitly because plain `python` selects 3.14 here:

```powershell
py -3.13 -m pip install --dry-run --ignore-installed --report .build/dependency-resolver-20260923.json -e ".[build,dev]"
py -3.13 -m pip install --upgrade --report .build/dependency-install-20260923.json -e ".[build,dev]"
py -3.13 -m pip check
py -3.13 -m pytest tests/test_dependency_pins.py tests/test_build_tool.py tests/test_pages_smoke.py -q
py -3.13 .tools/run_tests.py
py -3.13 .tools/run_lint.py
py -3.13 .tools/run_typecheck.py
py -3.13 .tools/run_compile.py
git diff --check
```

- Resolver, installation and `pip check`: passed. Local JSON reports are ignored build artifacts.
- Targeted dependency/build/pages run before the Pillow runtime addition: **164 passed**, including **34 build-tool tests**. Pin tests use PEP 508/440 parsers, reject non-exact/unstable pins, preserve Flet/Pillow consistency and the macOS-only marker, and guard reviewed minimum versions.
- Isolated source snapshot of `04678973124b6cc433e1863ebc740a6480d94b9b` plus these dependency changes: **161 targeted tests passed; 1,056 full tests passed; lint and typecheck passed** with the installed upgrades. Snapshot: `.build/dependency-audit-20260923/source`. Typecheck also reports a pre-existing missing `launcher/shared` include, without failing.
- Final integrated working-tree validation: **1,284 tests passed**; lint, typecheck (zero errors/warnings), compile, `pip check` and `git diff --check` passed. This includes menu navigation, shortcut icons, form layout, installed-mod updates and recursive dependency-selection regressions.
- macOS dependency-only resolution passed with `--dry-run --ignore-installed --only-binary=:all: --platform macosx_11_0_arm64 --python-version 3.13 --implementation cp --abi cp313 pyobjc-framework-AVFoundation==12.2.2`; all six PyObjC packages resolved to 12.2.2 universal2 wheels. This does not validate framework imports or permissions on macOS.
- Native macOS/Linux execution, AVFoundation permission prompts, signed installers and interactive desktop rendering were not validated on this Windows host. Windows build, archive inspection, offline runtime extraction and packaged smoke were validated as above. No commit, push, release or application version bump was performed.

Pillow runtime follow-up: `py -3.13 -m pytest tests/test_dependency_pins.py -q`
passed **12 tests**; the new runtime-dependency regression failed before the pin
was added. Runtime-only resolution with `py -3.13 -m pip install --dry-run
--ignore-installed --report .build/dependency-runtime-20260923.json -e .`
selected `pillow==12.3.0` without extras. Refreshed editable metadata using
`py -3.13 -m pip install --no-deps --no-build-isolation -e .`; Pillow was already
installed, so no library version changed. Installed metadata confirms its
unconditional runtime requirement; `PIL.Image`/`ImageOps` imports, `pip check`
and scoped Ruff checks passed. The Windows artifact described above predates
the shortcut feature and is not a release build of the final working tree.
