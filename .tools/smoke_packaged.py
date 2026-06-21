#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path


def _resolve_macos_binary(app_bundle: Path) -> Path:
    macos_dir = app_bundle / "Contents" / "MacOS"
    if not macos_dir.is_dir():
        raise FileNotFoundError(f"macOS app bundle is missing executable directory: {macos_dir}")

    for candidate in macos_dir.iterdir():
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"No executable found inside app bundle: {macos_dir}")


def _resolve_command(target: str, artifact: Path) -> list[str]:
    if target == "macos":
        return [str(_resolve_macos_binary(artifact)), "--smoke-test"]
    return [str(artifact), "--smoke-test"]


def _build_env(target: str, artifact: Path) -> dict[str, str]:
    env = os.environ.copy()
    if target == "linux" and artifact.suffix == ".AppImage":
        env.setdefault("APPIMAGE_EXTRACT_AND_RUN", "1")
    return env


def smoke_artifact(target: str, artifact: Path, timeout: int) -> None:
    command = _resolve_command(target, artifact)
    completed = subprocess.run(
        command,
        cwd=artifact.parent,
        env=_build_env(target, artifact),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if completed.returncode == 0:
        return

    stdout = completed.stdout.strip()
    stderr = completed.stderr.strip()
    details = []
    if stdout:
        details.append(f"stdout:\n{stdout}")
    if stderr:
        details.append(f"stderr:\n{stderr}")
    raise RuntimeError(
        f"Packaged smoke test failed for {artifact} with exit code {completed.returncode}\n"
        + "\n".join(details)
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run packaged runtime smoke tests for build artifacts.")
    parser.add_argument("--target", required=True, choices=["windows", "linux", "macos"])
    parser.add_argument("--artifact", action="append", required=True, help="Artifact path to smoke test.")
    parser.add_argument("--timeout", type=int, default=20)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    for artifact_arg in args.artifact:
        artifact = Path(artifact_arg).expanduser().resolve()
        if not artifact.exists():
            raise FileNotFoundError(f"Artifact not found: {artifact}")
        print(f"[smoke] {artifact}", flush=True)
        smoke_artifact(args.target, artifact, args.timeout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
