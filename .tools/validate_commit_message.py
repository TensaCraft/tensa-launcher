#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ALLOWED_TYPES = {
    "feat",
    "fix",
    "ui",
    "perf",
    "refactor",
    "docs",
    "test",
    "build",
    "ci",
    "chore",
    "release",
}
MIN_SUBJECT_LENGTH = 15
MAX_SUBJECT_LENGTH = 120
VAGUE_SUBJECTS = {
    "change",
    "changes",
    "fix",
    "misc",
    "update",
    "updates",
    "up",
    "wip",
}
COMMIT_PATTERN = re.compile(
    r"^(?P<type>[a-z]+)(?:\((?P<scope>[a-z0-9._/-]+)\))?(?P<breaking>!)?: (?P<subject>.+)$"
)


def _is_ascii(value: str) -> bool:
    try:
        value.encode("ascii")
    except UnicodeEncodeError:
        return False
    return True


def is_exempt_subject(subject: str) -> bool:
    return (
        subject.startswith("Merge ")
        or subject.startswith("Revert ")
        or subject.startswith("fixup! ")
        or subject.startswith("squash! ")
    )


def validate_subject(subject: str) -> list[str]:
    subject = subject.strip()
    if is_exempt_subject(subject):
        return []

    errors: list[str] = []
    match = COMMIT_PATTERN.match(subject)
    if not match:
        errors.append("Use Conventional Commit format: type(scope): English summary")
        return errors

    commit_type = match.group("type")
    summary = match.group("subject").strip()
    scope = match.group("scope")

    if commit_type not in ALLOWED_TYPES:
        errors.append(f"Unsupported commit type '{commit_type}'")
    if scope and not scope.islower():
        errors.append("Commit scope must be lowercase")
    if len(summary) < MIN_SUBJECT_LENGTH:
        errors.append(f"Commit summary must be at least {MIN_SUBJECT_LENGTH} characters")
    if len(summary) > MAX_SUBJECT_LENGTH:
        errors.append(f"Commit summary must be at most {MAX_SUBJECT_LENGTH} characters")
    if summary.lower().strip(".") in VAGUE_SUBJECTS:
        errors.append("Commit summary is too vague for release notes")
    if not _is_ascii(subject):
        errors.append("Commit subject must be English ASCII text")

    return errors


def read_commit_subject(path: Path) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped
    return ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate TensaLauncher commit message format.")
    parser.add_argument("message_file", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    subject = read_commit_subject(args.message_file)
    errors = validate_subject(subject)
    if not errors:
        return 0

    print("Invalid commit message:", file=sys.stderr)
    print(f"  {subject or '<empty>'}", file=sys.stderr)
    for error in errors:
        print(f"- {error}", file=sys.stderr)
    print("", file=sys.stderr)
    print("Examples:", file=sys.stderr)
    print("  fix(updater): select stable release when newer than beta", file=sys.stderr)
    print("  ui(home): balance version card title and icon spacing", file=sys.stderr)
    print("  release: bump version to 4.0.11", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
