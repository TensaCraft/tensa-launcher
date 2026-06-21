#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import urllib.request
from dataclasses import dataclass
from pathlib import Path

CONVENTIONAL_PATTERN = re.compile(
    r"^(?P<type>[a-z]+)(?:\((?P<scope>[a-z0-9._/-]+)\))?(?P<breaking>!)?: (?P<subject>.+)$"
)
NOTE_TYPES = {
    "feat": "New Features",
    "fix": "Fixes",
    "ui": "Interface",
    "perf": "Performance",
}
SECTION_ORDER = ["New Features", "Fixes", "Interface", "Performance", "Other Changes"]
OMITTED_TYPES = {"build", "chore", "ci", "docs", "refactor", "release", "test"}
OMITTED_SCOPES = {"msix", "store"}
OMITTED_SUBJECT_KEYWORDS = ("microsoft store", "msix", "store package")
MIN_NOTE_LENGTH = 10
VAGUE_SUBJECTS = {"change", "changes", "fix", "misc", "update", "updates", "up", "wip"}
PLAIN_OMIT_PREFIXES = ("bump version", "release v", "release version")
TAG_PATTERN = re.compile(r"^v[0-9].*")


@dataclass(frozen=True)
class CommitEntry:
    sha: str
    subject: str


@dataclass(frozen=True)
class ReleaseNote:
    section: str
    text: str


def run_git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True, encoding="utf-8").strip()


def resolve_ref(ref: str) -> str:
    return run_git("rev-parse", f"{ref}^{{commit}}")


def previous_git_tag(target_commit: str) -> str | None:
    try:
        return run_git("describe", "--tags", "--abbrev=0", "--match", "v[0-9]*", f"{target_commit}^")
    except subprocess.CalledProcessError:
        return None


def github_releases(repo: str, token: str) -> list[dict[str, object]]:
    releases: list[dict[str, object]] = []
    page = 1
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "tensalauncher-release-notes",
    }
    while True:
        url = f"https://api.github.com/repos/{repo}/releases?per_page=100&page={page}"
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.load(response)
        if not payload:
            return releases
        releases.extend(payload)
        page += 1


def is_ancestor(older_ref: str, newer_ref: str) -> bool:
    return subprocess.run(
        ["git", "merge-base", "--is-ancestor", older_ref, newer_ref],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode == 0


def previous_stable_release_tag(target_ref: str, *, repo: str | None, token: str | None) -> str | None:
    if not repo or not token:
        return None

    target_commit = resolve_ref(target_ref)
    for release in github_releases(repo, token):
        tag = str(release.get("tag_name") or "")
        if not tag or tag == target_ref or not TAG_PATTERN.match(tag):
            continue
        if release.get("draft") or release.get("prerelease"):
            continue
        try:
            tag_commit = resolve_ref(tag)
        except subprocess.CalledProcessError:
            continue
        if tag_commit != target_commit and is_ancestor(tag_commit, target_commit):
            return tag
    return None


def previous_tag(target_ref: str, *, stable_release: bool = False, repo: str | None = None, token: str | None = None) -> str | None:
    target_commit = resolve_ref(target_ref)
    if stable_release:
        stable_tag = previous_stable_release_tag(target_ref, repo=repo, token=token)
        if stable_tag:
            return stable_tag
    return previous_git_tag(target_commit)


def commit_range(target_ref: str, previous: str | None) -> str:
    target_commit = resolve_ref(target_ref)
    if previous:
        return f"{previous}..{target_commit}"
    return target_commit


def collect_commits(target_ref: str, previous: str | None) -> list[CommitEntry]:
    raw = run_git("log", "--format=%H%x00%s", commit_range(target_ref, previous))
    commits: list[CommitEntry] = []
    for line in raw.splitlines():
        if not line:
            continue
        sha, _, subject = line.partition("\0")
        commits.append(CommitEntry(sha=sha, subject=subject.strip()))
    return commits


def humanize_scope(scope: str | None) -> str:
    if not scope:
        return ""
    return scope.replace("-", " ").replace("_", " ").replace("/", " ").replace(".", " ").title()


def humanize_subject(subject: str) -> str:
    cleaned = subject.strip().rstrip(".")
    if not cleaned:
        return ""
    return f"{cleaned[0].upper()}{cleaned[1:]}"


def is_useful_subject(subject: str) -> bool:
    cleaned = subject.strip().lower().rstrip(".")
    if len(cleaned) < MIN_NOTE_LENGTH or cleaned in VAGUE_SUBJECTS:
        return False
    return not any(cleaned.startswith(prefix) for prefix in PLAIN_OMIT_PREFIXES)


def is_omitted_release_note(scope: str | None, subject: str) -> bool:
    cleaned_scope = (scope or "").strip().lower()
    cleaned_subject = subject.strip().lower()

    return cleaned_scope in OMITTED_SCOPES or any(keyword in cleaned_subject for keyword in OMITTED_SUBJECT_KEYWORDS)


def note_from_commit(commit: CommitEntry) -> ReleaseNote | None:
    subject = commit.subject.strip()
    if subject.startswith(("Merge ", "Revert ", "fixup! ", "squash! ")):
        return None

    match = CONVENTIONAL_PATTERN.match(subject)
    if match:
        commit_type = match.group("type")
        summary = match.group("subject").strip()
        if commit_type in OMITTED_TYPES or not is_useful_subject(summary):
            return None
        raw_scope = match.group("scope")
        if is_omitted_release_note(raw_scope, summary):
            return None
        scope = humanize_scope(raw_scope)
        text = humanize_subject(summary)
        if scope:
            text = f"**{scope}:** {text}"
        return ReleaseNote(section=NOTE_TYPES.get(commit_type, "Other Changes"), text=text)

    if not is_useful_subject(subject):
        return None
    return ReleaseNote(section="Other Changes", text=humanize_subject(subject))


def build_notes(commits: list[CommitEntry], *, tag: str, previous: str | None) -> str:
    grouped: dict[str, list[str]] = {section: [] for section in SECTION_ORDER}
    for commit in reversed(commits):
        note = note_from_commit(commit)
        if note is None:
            continue
        bucket = grouped.setdefault(note.section, [])
        if note.text not in bucket:
            bucket.append(note.text)

    lines = [f"# {tag}", ""]
    if previous:
        lines.extend([f"Changes since `{previous}`.", ""])

    wrote_section = False
    for section in SECTION_ORDER:
        entries = grouped.get(section, [])
        if not entries:
            continue
        wrote_section = True
        lines.extend([f"## {section}", ""])
        lines.extend(f"- {entry}" for entry in entries)
        lines.append("")

    if not wrote_section:
        lines.extend(["## Changes", "", "- Maintenance release.", ""])

    return "\n".join(lines).rstrip() + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate GitHub release notes from commit messages.")
    parser.add_argument("--tag", default="HEAD", help="Release tag or ref. Defaults to HEAD.")
    parser.add_argument("--output", type=Path, default=Path("RELEASE_NOTES.md"))
    parser.add_argument(
        "--stable-release",
        action="store_true",
        help="Use the previous non-prerelease GitHub release as the changelog base when possible.",
    )
    parser.add_argument("--github-repo", default=os.getenv("GITHUB_REPOSITORY"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    previous = previous_tag(
        args.tag,
        stable_release=args.stable_release,
        repo=args.github_repo,
        token=os.getenv("GITHUB_TOKEN"),
    )
    commits = collect_commits(args.tag, previous)
    notes = build_notes(commits, tag=args.tag, previous=previous)
    args.output.write_text(notes, encoding="utf-8")
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
