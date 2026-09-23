from __future__ import annotations

import json
import string
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from launcher.application import modrinth_inventory
from launcher.application.mod_identity import ModIdentityService, ModMatch, ModMatchKind
from launcher.core.api.modrinth import ModrinthAPI


@dataclass(slots=True)
class ModInstallFile:
    url: str
    filename: str
    version_number: str = ""
    size: int = 0
    file_hash: str = ""
    hash_algorithm: str = ""


@dataclass(slots=True)
class ModrinthInstallCandidate:
    project: dict[str, Any]
    version_data: dict[str, Any]
    install_file: ModInstallFile
    action: str
    dependency_type: str = "selected"
    requested_by: tuple[str, ...] = ()
    installed_item: dict[str, Any] | None = None
    installed_match: ModMatch | None = None

    @property
    def project_id(self) -> str:
        return str(self.project.get("project_id") or self.version_data.get("project_id") or "")

    @property
    def title(self) -> str:
        return str(self.project.get("title") or self.project.get("slug") or self.project_id or self.install_file.filename)

    @property
    def version_id(self) -> str:
        return str(self.version_data.get("id") or "")

    @property
    def version_number(self) -> str:
        return str(self.version_data.get("version_number") or self.install_file.version_number or "")

    @property
    def page_url(self) -> str | None:
        slug = str(self.project.get("slug") or "").strip()
        if not slug and self.project.get("_resolved") is False:
            return None
        identifier = slug or self.project_id
        if not identifier:
            return None
        project_type = str(self.project.get("project_type") or "mod").strip() or "mod"
        return f"https://modrinth.com/{project_type}/{identifier}"


@dataclass(slots=True)
class ModrinthDependencyIssue:
    code: str
    message: str
    blocking: bool = True
    project_id: str | None = None
    project_title: str | None = None
    project_slug: str | None = None
    project_type: str | None = None
    project_url: str | None = None
    version_id: str | None = None
    file_name: str | None = None
    dependency_type: str = ""
    requested_by: tuple[str, ...] = ()

    @property
    def display_name(self) -> str | None:
        return self.project_title or self.project_id or self.file_name or self.version_id


@dataclass(slots=True)
class ModrinthDependencyPlan:
    main: ModrinthInstallCandidate | None
    dependencies_to_install: list[ModrinthInstallCandidate]
    dependencies_to_replace: list[ModrinthInstallCandidate]
    already_satisfied: list[ModrinthInstallCandidate]
    optional_dependencies: list[ModrinthInstallCandidate]
    skipped_embedded: list[ModrinthDependencyIssue]
    blocking_issues: list[ModrinthDependencyIssue]
    optional_dependency_issues: list[ModrinthDependencyIssue] = field(default_factory=list)
    include_implicit_dependencies: bool = False
    selected_optional_dependencies: list[ModrinthInstallCandidate] = field(default_factory=list)

    @property
    def requires_confirmation(self) -> bool:
        selectable_optional = any(candidate.action != "satisfied" for candidate in self.optional_dependencies)
        return bool(
            self.dependencies_to_install
            or self.dependencies_to_replace
            or selectable_optional
            or self.blocking_issues
        )

    @property
    def can_install(self) -> bool:
        return self.main is not None and not self.blocking_issues

    @property
    def install_order(self) -> list[ModrinthInstallCandidate]:
        return self.install_order_with_optional()

    def install_order_with_optional(
        self,
        selected_optional_dependencies: list[ModrinthInstallCandidate] | None = None,
    ) -> list[ModrinthInstallCandidate]:
        if self.main is None:
            return []
        selected = [candidate for candidate in selected_optional_dependencies or [] if candidate.action != "satisfied"]
        required_ids = {candidate.project_id for candidate in self.dependencies_to_replace + self.dependencies_to_install}
        required_ids.update(candidate.project_id for candidate in self.already_satisfied if candidate.dependency_type != "optional")
        required_ids.add(self.main.project_id)
        unique_optional = [candidate for candidate in selected if candidate.project_id not in required_ids]
        return [*self.dependencies_to_replace, *self.dependencies_to_install, *unique_optional, self.main]


class ModrinthModsService:
    LOADER_SCOPED_PROJECT_TYPES = {"mod"}
    FABRIC_API_PROJECT_ID = "P7dR8mSH"

    def __init__(self, identity: ModIdentityService | None = None) -> None:
        self.identity = identity or ModIdentityService()

    def identify_installed_items(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return modrinth_inventory.identify_installed_items(items)

    def check_installed_updates(self, installed_items: list[dict[str, Any]], version) -> list[dict[str, Any]]:
        identified = self.identify_installed_items(installed_items)
        loader = self.get_loader_name(version)
        game_version = getattr(version, "version", None)
        eligible = [
            item for item in identified
            if item.get("enabled", True)
            and item.get("modrinth_hash_algorithm") == "sha512"
            and self.owns_installed_item(item, str(item.get("modrinth_project_id") or ""))
        ]
        updates = ModrinthAPI.get_updates_by_hashes(
            [item["modrinth_file_hash"] for item in eligible],
            game_versions=[game_version] if game_version else [],
            loaders=[loader] if loader else [],
        ) if eligible else {}
        for item in identified:
            item["update_available"] = False
            item["update_checked"] = False
            item.pop("latest_version", None)
        for item in eligible:
            candidate = updates.get(item["modrinth_file_hash"])
            item["update_checked"] = candidate is not None
            if not candidate or candidate.get("id") == item.get("modrinth_version_id"):
                continue
            if candidate.get("project_id") != item.get("modrinth_project_id"):
                raise ValueError("Modrinth returned an update for a different project")
            current = item.get("modrinth_version_data")
            if not isinstance(current, dict):
                current = ModrinthAPI.get_version_by_id(item["modrinth_version_id"])
            if (
                self.filter_compatible_versions([candidate], version)
                and self.select_primary_file(candidate) is not None
                and self._version_date(candidate) > self._version_date(current)
            ):
                item["update_available"] = True
                item["latest_version"] = candidate
        return identified

    @staticmethod
    def get_loader_name(version) -> str | None:
        loader = (getattr(version, "loader", "") or "").lower()
        if not loader:
            loader = (getattr(version, "client", "") or "").lower()

        for loader_name in ("fabric", "neoforge", "forge", "quilt"):
            if loader_name in loader:
                return loader_name
        return None

    def build_search_facets(
        self,
        version,
        *,
        project_type: str = "mod",
        game_version: str | None = None,
    ) -> str:
        loader = self.get_loader_name(version) if project_type in self.LOADER_SCOPED_PROJECT_TYPES else None
        target_game_version = game_version or getattr(version, "version", None)
        facets: list[list[str]] = [[f"project_type:{project_type}"]]

        if loader:
            facets.append([f"categories:{loader}"])
        if target_game_version:
            facets.append([f"versions:{target_game_version}"])

        return json.dumps(facets)

    def get_compatible_versions(
        self,
        project_id: str,
        version,
        *,
        project_type: str = "mod",
        game_version: str | None = None,
    ) -> list[dict[str, Any]]:
        loader = self.get_loader_name(version) if project_type in self.LOADER_SCOPED_PROJECT_TYPES else None
        target_game_version = game_version or getattr(version, "version", None)
        loaders = [loader] if loader else None
        game_versions = [target_game_version] if target_game_version else None
        versions = ModrinthAPI.get_mod_versions(project_id, game_versions, loaders)
        return self.filter_compatible_versions(
            versions,
            version,
            project_type=project_type,
            game_version=target_game_version,
        )

    def find_latest_version(
        self,
        project_id: str,
        version,
        *,
        project_type: str = "mod",
        game_version: str | None = None,
    ) -> dict[str, Any] | None:
        versions = self.get_compatible_versions(
            project_id,
            version,
            project_type=project_type,
            game_version=game_version,
        )
        return versions[0] if versions else None

    def build_dependency_plan(
        self,
        project: dict[str, Any],
        version,
        *,
        project_type: str = "mod",
        game_version: str | None = None,
        installed_items: list[dict[str, Any]] | None = None,
        include_implicit_dependencies: bool = True,
    ) -> ModrinthDependencyPlan:
        installed = self.identify_installed_items(installed_items or [])
        normalized_project = self._normalize_project_payload(project)
        project_id = str(normalized_project.get("project_id") or "")
        main_version = self.find_latest_version(
            project_id,
            version,
            project_type=project_type,
            game_version=game_version,
        )
        if main_version is None:
            return self._empty_dependency_plan(
                ModrinthDependencyIssue(
                    "no_compatible_version",
                    "No compatible Modrinth version found.",
                    project_id=project_id or None,
                )
            )

        main_file = self.select_primary_file(main_version)
        if main_file is None:
            return self._empty_dependency_plan(
                ModrinthDependencyIssue(
                    "no_primary_file",
                    "No downloadable file found for this Modrinth version.",
                    project_id=project_id or None,
                    version_id=str(main_version.get("id") or "") or None,
                )
            )

        main_candidate = self._candidate_for(
            normalized_project,
            main_version,
            main_file,
            installed,
            version,
            project_type=project_type,
            game_version=game_version,
            dependency_type="selected",
        )
        ambiguity = self._ambiguous_installation_issue(main_candidate)
        if ambiguity is not None:
            return self._empty_dependency_plan(ambiguity)
        if project_type != "mod":
            return ModrinthDependencyPlan(main_candidate, [], [], [], [], [], [])

        return self._resolve_required_dependencies(
            main_candidate,
            version,
            project_type=project_type,
            game_version=game_version,
            installed_items=installed,
            include_implicit_dependencies=include_implicit_dependencies,
        )

    def resolve_optional_dependencies(
        self,
        plan: ModrinthDependencyPlan,
        selected: list[ModrinthInstallCandidate],
        version,
        *,
        installed_items: list[dict[str, Any]],
        project_type: str = "mod",
        game_version: str | None = None,
        include_implicit_dependencies: bool | None = None,
    ) -> ModrinthDependencyPlan:
        """Replan the original main plus prior and new exact optional selections."""
        if plan.main is None:
            return replace(plan)
        installed = self.identify_installed_items(installed_items)
        main = self._candidate_for(
            plan.main.project, plan.main.version_data, plan.main.install_file, installed, version,
            project_type=project_type, game_version=game_version, dependency_type="selected",
            exact_version=True,
        )
        ambiguity = self._ambiguous_installation_issue(main)
        if ambiguity is not None:
            return self._empty_dependency_plan(ambiguity)
        if project_type != "mod":
            return ModrinthDependencyPlan(main, [], [], [], [], [], [])
        roots: dict[tuple[str, str], ModrinthInstallCandidate] = {}
        for candidate in [*plan.selected_optional_dependencies, *selected]:
            roots.setdefault((candidate.project_id, candidate.version_id), candidate)
        selected_roots = list(roots.values())
        implicit = plan.include_implicit_dependencies if include_implicit_dependencies is None else include_implicit_dependencies
        for candidate in selected_roots:
            if not candidate.project_id or not candidate.version_id:
                return ModrinthDependencyPlan(main, [], [], [], [], [], [
                    ModrinthDependencyIssue(
                        "dependency_resolution_failed", "Selected dependency has no exact version.",
                        project_id=candidate.project_id or None,
                    ),
                ], include_implicit_dependencies=implicit, selected_optional_dependencies=selected_roots)
        implicit_candidate = next((
            candidate for candidate in plan.dependencies_to_install + plan.dependencies_to_replace + plan.already_satisfied
            if candidate.project_id == self.FABRIC_API_PROJECT_ID and candidate.dependency_type != "optional"
        ), None)
        return self._resolve_required_dependencies(
            main, version, project_type=project_type, game_version=game_version,
            installed_items=installed,
            include_implicit_dependencies=implicit,
            selected_optional_dependencies=selected_roots,
            implicit_candidate=implicit_candidate,
        )

    @staticmethod
    def select_primary_file(version_data: dict[str, Any] | None) -> ModInstallFile | None:
        if not version_data:
            return None

        files = version_data.get("files", [])
        if not files:
            return None

        ordered_files = sorted(
            (file for file in files if isinstance(file, dict)),
            key=lambda file: not bool(file.get("primary")),
        )
        for mod_file in ordered_files:
            install_file = ModrinthModsService._validated_install_file(mod_file, version_data)
            if install_file is not None:
                return install_file
        return None

    @staticmethod
    def _validated_install_file(
        mod_file: dict[str, Any],
        version_data: dict[str, Any],
    ) -> ModInstallFile | None:
        url = str(mod_file.get("url") or "").strip()
        filename = str(mod_file.get("filename") or "").strip()
        size = mod_file.get("size")
        if not ModrinthModsService._is_secure_download_url(url):
            return None
        if not filename or "\x00" in filename:
            return None
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
            return None

        hashes = mod_file.get("hashes")
        if not isinstance(hashes, dict):
            return None
        hash_algorithm, file_hash = ModrinthModsService._strongest_supported_hash(hashes)
        if not file_hash:
            return None

        return ModInstallFile(
            url=url,
            filename=filename,
            version_number=str(version_data.get("version_number") or ""),
            size=size,
            file_hash=file_hash,
            hash_algorithm=hash_algorithm,
        )

    @staticmethod
    def _is_secure_download_url(url: str) -> bool:
        try:
            parsed = urlsplit(url)
            _ = parsed.port
        except ValueError:
            return False
        return bool(
            parsed.scheme.lower() == "https"
            and parsed.hostname
            and parsed.username is None
            and parsed.password is None
        )

    @staticmethod
    def _strongest_supported_hash(hashes: dict[str, Any]) -> tuple[str, str]:
        for algorithm, digest_length in (("sha512", 128), ("sha1", 40)):
            digest = str(hashes.get(algorithm) or "").strip().lower()
            if len(digest) == digest_length and all(char in string.hexdigits for char in digest):
                return algorithm, digest
        return "", ""

    @staticmethod
    def is_installed(installed_mods: list[dict[str, Any]], project: dict[str, Any]) -> bool:
        return ModrinthModsService.find_installed(installed_mods, project) is not None

    @staticmethod
    def find_installed(installed_mods: list[dict[str, Any]], project: dict[str, Any]) -> dict[str, Any] | None:
        match = ModIdentityService.match_project(installed_mods, project)
        return match.item if isinstance(match.item, dict) else None

    def match_installed(
        self,
        installed_items: list[dict[str, Any]],
        project: dict[str, Any],
    ) -> ModMatch:
        project_id = str(project.get("project_id") or project.get("id") or "").strip()
        enabled_owned = [
            item for item in installed_items
            if self.identity.owns_item(item, project_id) and self._installed_item_enabled(item)
        ]
        if len(enabled_owned) == 1:
            return self.identity.match_project(enabled_owned, project)
        return self.identity.match_project(installed_items, project)

    @staticmethod
    def _installed_item_enabled(item: Mapping[str, Any]) -> bool:
        return bool(item.get("enabled", True)) and not str(item.get("path") or "").lower().endswith(".disabled")

    @staticmethod
    def owns_installed_item(installed_item: dict[str, Any] | None, project_id: str) -> bool:
        return ModIdentityService.owns_item(installed_item, project_id)

    @staticmethod
    def _normalize_identifier(value: Any) -> str:
        return ModIdentityService.normalize_identifier(value)

    def filter_compatible_versions(
        self,
        versions: list[dict[str, Any]] | None,
        version,
        *,
        project_type: str = "mod",
        game_version: str | None = None,
    ) -> list[dict[str, Any]]:
        loader = self.get_loader_name(version) if project_type in self.LOADER_SCOPED_PROJECT_TYPES else None
        target_game_version = game_version or getattr(version, "version", None)
        compatible_versions: list[dict[str, Any]] = []

        for version_data in versions or []:
            version_game_versions = version_data.get("game_versions", [])
            version_loaders = version_data.get("loaders", [])
            game_version_match = not target_game_version or target_game_version in version_game_versions
            loader_match = not loader or loader in version_loaders
            if game_version_match and loader_match:
                compatible_versions.append(version_data)

        return compatible_versions

    @staticmethod
    def _empty_dependency_plan(issue: ModrinthDependencyIssue) -> ModrinthDependencyPlan:
        return ModrinthDependencyPlan(None, [], [], [], [], [], [issue])

    def _resolve_required_dependencies(
        self,
        main_candidate: ModrinthInstallCandidate,
        version,
        *,
        project_type: str,
        game_version: str | None,
        installed_items: list[dict[str, Any]],
        include_implicit_dependencies: bool,
        selected_optional_dependencies: list[ModrinthInstallCandidate] | None = None,
        implicit_candidate: ModrinthInstallCandidate | None = None,
    ) -> ModrinthDependencyPlan:
        selected = selected_optional_dependencies or []
        selected_ids = {candidate.project_id for candidate in selected}
        root_dependencies = [
            dependency for dependency in main_candidate.version_data.get("dependencies", [])
            if isinstance(dependency, dict)
        ] + [
            {"project_id": candidate.project_id, "version_id": candidate.version_id, "dependency_type": "required"}
            for candidate in selected
        ]
        cache: dict[tuple[str, ...], tuple[ModrinthInstallCandidate | None, list[ModrinthDependencyIssue]]] = {}

        def resolve(dependency, requested_by, issues, *, optional=False):
            key = tuple(str(dependency.get(name) or "") for name in ("project_id", "version_id", "file_name")) + (str(optional),)
            if key not in cache:
                errors: list[ModrinthDependencyIssue] = []
                candidate = self._resolve_dependency_candidate(
                    dependency, version, project_type=project_type, game_version=game_version,
                    installed_items=installed_items, requested_by=(), issues=errors,
                    dependency_kind="optional" if optional else "required", blocking=not optional,
                    known_candidate=(
                        implicit_candidate if dependency.get("project_id") == self.FABRIC_API_PROJECT_ID
                        and not dependency.get("version_id") else None
                    ),
                )
                cache[key] = candidate, errors
            candidate, errors = cache[key]
            issues.extend(replace(issue, requested_by=requested_by) for issue in errors)
            return replace(candidate, requested_by=requested_by) if candidate is not None else None

        previous: dict[str, ModrinthInstallCandidate] = {}
        seen: set[tuple[tuple[str, str], ...]] = {()}
        while True:
            # Only the currently reachable versions contribute constraints on each pass.
            resolved: dict[str, ModrinthInstallCandidate] = {}
            pinned = {main_candidate.project_id: main_candidate.version_id}
            optional: dict[str, ModrinthInstallCandidate] = {}
            optional_satisfied: list[ModrinthInstallCandidate] = []
            optional_issues: list[ModrinthDependencyIssue] = []
            embedded: list[ModrinthDependencyIssue] = []
            issues: list[ModrinthDependencyIssue] = []
            incompatible: list[tuple[dict[str, Any], tuple[str, ...]]] = []
            traversed = {main_candidate.project_id}
            queue = deque((main_candidate, dependency) for dependency in root_dependencies)
            implicit_checked = not (
                include_implicit_dependencies and project_type == "mod"
                and self.get_loader_name(version) == "fabric"
                and main_candidate.project_id != self.FABRIC_API_PROJECT_ID
            )

            while queue or not implicit_checked:
                if not queue:
                    implicit_checked = True
                    if self.FABRIC_API_PROJECT_ID in resolved:
                        break
                    queue.append((main_candidate, {"project_id": self.FABRIC_API_PROJECT_ID, "dependency_type": "required"}))
                source, dependency = queue.popleft()
                kind = str(dependency.get("dependency_type") or "required").lower()
                requested_by = (*source.requested_by, source.title)
                if kind == "optional":
                    if str(dependency.get("project_id") or "") in selected_ids:
                        continue
                    candidate = resolve(dependency, requested_by, optional_issues, optional=True)
                    if candidate is None or candidate.project_id == main_candidate.project_id or candidate.project_id in selected_ids:
                        continue
                    if candidate.action == "satisfied":
                        optional_satisfied.append(candidate)
                    else:
                        existing = optional.get(candidate.project_id)
                        if existing is None or self._is_version_newer(candidate.version_data, existing.version_data):
                            optional[candidate.project_id] = candidate
                    continue
                if kind == "embedded":
                    embedded.append(self._issue_for_dependency("embedded_dependency", dependency, requested_by, blocking=False))
                    continue
                if kind == "incompatible":
                    incompatible.append((dependency, requested_by))
                    continue
                if kind != "required":
                    optional_issues.append(self._issue_for_dependency("unsupported_dependency_type", dependency, requested_by, blocking=False))
                    continue

                candidate = resolve(dependency, requested_by, issues)
                if candidate is None:
                    continue
                exact_id = str(dependency.get("version_id") or "")
                pinned_id = pinned.get(candidate.project_id)
                if exact_id and pinned_id and exact_id != pinned_id:
                    issues.append(self._issue_for_dependency(
                        "dependency_version_conflict", dependency, requested_by, project_id=candidate.project_id,
                    ))
                    continue
                if candidate.project_id == main_candidate.project_id:
                    continue
                if exact_id:
                    pinned[candidate.project_id] = exact_id
                existing = resolved.get(candidate.project_id)
                if existing is None or exact_id or (
                    not pinned_id and self._is_version_newer(candidate.version_data, existing.version_data)
                ):
                    resolved[candidate.project_id] = candidate
                if candidate.project_id in traversed:
                    continue
                traversed.add(candidate.project_id)
                chosen = replace(previous.get(candidate.project_id, candidate), requested_by=requested_by)
                for child in chosen.version_data.get("dependencies", []):
                    if isinstance(child, dict):
                        queue.append((chosen, child))

            signature = tuple(sorted((key, candidate.version_id) for key, candidate in resolved.items()))
            old_signature = tuple(sorted((key, candidate.version_id) for key, candidate in previous.items()))
            if signature != old_signature:
                if signature not in seen:
                    seen.add(signature)
                    previous = resolved
                    continue
                issues.append(ModrinthDependencyIssue(
                    "dependency_version_conflict", "Dependency version selections do not converge.",
                    project_id=main_candidate.project_id,
                ))
            self._append_incompatible_issues(incompatible, main_candidate, resolved, installed_items, issues)
            candidates = list(resolved.values())
            return ModrinthDependencyPlan(
                main=main_candidate,
                dependencies_to_install=[candidate for candidate in candidates if candidate.action == "install"],
                dependencies_to_replace=[candidate for candidate in candidates if candidate.action == "replace"],
                already_satisfied=[candidate for candidate in candidates if candidate.action == "satisfied"] + [
                    candidate for candidate in optional_satisfied if candidate.project_id not in resolved
                ],
                optional_dependencies=list(optional.values()), skipped_embedded=embedded,
                blocking_issues=issues, optional_dependency_issues=optional_issues,
                include_implicit_dependencies=include_implicit_dependencies,
                selected_optional_dependencies=list(selected),
            )

    def _resolve_dependency_candidate(
        self,
        dependency: dict[str, Any],
        version,
        *,
        project_type: str,
        game_version: str | None,
        installed_items: list[dict[str, Any]],
        requested_by: tuple[str, ...],
        issues: list[ModrinthDependencyIssue],
        dependency_kind: str,
        blocking: bool,
        known_candidate: ModrinthInstallCandidate | None = None,
    ) -> ModrinthInstallCandidate | None:
        version_id = str(dependency.get("version_id") or "")
        project_id = str(dependency.get("project_id") or "")
        file_name = str(dependency.get("file_name") or "")
        version_data: dict[str, Any] | None = None

        if version_id:
            try:
                exact_version = ModrinthAPI.get_version_by_id(version_id)
            except Exception:
                exact_version = None
            if not isinstance(exact_version, dict) or str(exact_version.get("id") or "") != version_id:
                issues.append(
                    self._issue_for_dependency(
                        "dependency_resolution_failed",
                        dependency,
                        requested_by,
                        blocking=blocking,
                        project_id=project_id or None,
                        version_id=version_id,
                    )
                )
                return None

            exact_project_id = str(exact_version.get("project_id") or "")
            if not exact_project_id or (project_id and exact_project_id != project_id):
                issues.append(
                    self._issue_for_dependency(
                        "dependency_project_mismatch",
                        dependency,
                        requested_by,
                        blocking=blocking,
                        project_id=project_id or exact_project_id or None,
                        version_id=version_id,
                    )
                )
                return None

            project_id = exact_project_id
            if not self.filter_compatible_versions(
                [exact_version],
                version,
                project_type=project_type,
                game_version=game_version,
            ):
                issues.append(
                    self._issue_for_dependency(
                        "dependency_incompatible",
                        dependency,
                        requested_by,
                        blocking=blocking,
                        project_id=project_id,
                        version_id=version_id,
                    )
                )
                return None
            version_data = exact_version
        elif project_id:
            match = self.match_installed(installed_items, {"project_id": project_id})
            if match.owned and match.item and self._installed_item_enabled(match.item):
                current = self._installed_version(match.item)
                if current and current.get("project_id") == project_id and self.filter_compatible_versions(
                    [current], version, project_type=project_type, game_version=game_version,
                ):
                    version_data = current
            if version_data is None and known_candidate is not None and self.filter_compatible_versions(
                [known_candidate.version_data], version, project_type=project_type, game_version=game_version,
            ):
                version_data = known_candidate.version_data
            if version_data is None:
                versions = self._get_compatible_versions_safely(
                    project_id,
                    version,
                    project_type=project_type,
                    game_version=game_version,
                )
                version_data = self._select_newest_version(versions)
            if version_data is None:
                issues.append(
                    self._issue_for_dependency(
                        "dependency_resolution_failed",
                        dependency,
                        requested_by,
                        blocking=blocking,
                        project_id=project_id,
                    )
                )
                return None
        else:
            issues.append(
                self._issue_for_dependency(
                    "required_file_only",
                    dependency,
                    requested_by,
                    file_name=file_name or None,
                    blocking=blocking,
                )
            )
            return None

        if version_data is None:
            issues.append(
                self._issue_for_dependency(
                    "dependency_resolution_failed",
                    dependency,
                    requested_by,
                    blocking=blocking,
                    project_id=project_id or None,
                    version_id=version_id or None,
                )
            )
            return None

        project_id = project_id or str(version_data.get("project_id") or "")
        install_file = self.select_primary_file(version_data)
        if install_file is None:
            issues.append(
                self._issue_for_dependency(
                    "dependency_no_file",
                    dependency,
                    requested_by,
                    blocking=blocking,
                    project_id=project_id or None,
                    version_id=str(version_data.get("id") or "") or None,
                )
            )
            return None

        project = known_candidate.project if known_candidate is not None else self._load_project(
            project_id or str(version_data.get("project_id") or ""),
        )
        candidate = self._candidate_for(
            project,
            version_data,
            install_file,
            installed_items,
            version,
            project_type=project_type,
            game_version=game_version,
            dependency_type=dependency_kind,
            requested_by=requested_by,
            exact_version=bool(version_id),
        )
        ambiguity = self._ambiguous_installation_issue(candidate, blocking=blocking)
        if ambiguity is not None:
            issues.append(ambiguity)
            return None
        return candidate

    def _ambiguous_installation_issue(
        self, candidate: ModrinthInstallCandidate, *, blocking: bool = True,
    ) -> ModrinthDependencyIssue | None:
        match = candidate.installed_match
        if (
            match is None or match.kind != ModMatchKind.AMBIGUOUS
            or match.reason != "duplicate_verified_modrinth_provenance"
        ):
            return None
        return self._issue_for_dependency(
            "dependency_resolution_failed",
            {
                "project_id": candidate.project_id,
                "version_id": candidate.version_id,
                "dependency_type": candidate.dependency_type,
            },
            candidate.requested_by,
            blocking=blocking,
        )

    def _get_compatible_versions_safely(
        self,
        project_id: str,
        version,
        *,
        project_type: str,
        game_version: str | None,
    ) -> list[dict[str, Any]]:
        try:
            return self.get_compatible_versions(
                project_id,
                version,
                project_type=project_type,
                game_version=game_version,
            )
        except Exception:
            return []

    def _candidate_for(
        self,
        project: dict[str, Any],
        version_data: dict[str, Any],
        install_file: ModInstallFile,
        installed_items: list[dict[str, Any]],
        version,
        *,
        project_type: str,
        game_version: str | None,
        dependency_type: str,
        requested_by: tuple[str, ...] = (),
        exact_version: bool = False,
    ) -> ModrinthInstallCandidate:
        normalized_project = self._normalize_project_payload(
            project,
            fallback_project_id=str(version_data.get("project_id") or ""),
        )
        installed_match = self.match_installed(installed_items, normalized_project)
        installed_item = installed_match.item if isinstance(installed_match.item, dict) else None
        action = self._candidate_action(
            installed_match,
            version_data,
            version,
            project_type=project_type,
            game_version=game_version,
            exact_version=exact_version,
        )
        return ModrinthInstallCandidate(
            project=normalized_project,
            version_data=version_data,
            install_file=install_file,
            action=action,
            dependency_type=dependency_type,
            requested_by=requested_by,
            installed_item=installed_item,
            installed_match=installed_match,
        )

    def _candidate_action(
        self,
        installed_match: ModMatch,
        candidate_version: dict[str, Any],
        version,
        *,
        project_type: str,
        game_version: str | None,
        exact_version: bool = False,
    ) -> str:
        installed_item = installed_match.item
        if installed_item is None:
            return "install"
        if not installed_match.owned:
            return "install"
        if not self._installed_item_enabled(installed_item):
            return "replace"

        installed_version_id = str(installed_item.get("modrinth_version_id") or "")
        candidate_version_id = str(candidate_version.get("id") or "")
        if installed_version_id and installed_version_id == candidate_version_id:
            return "satisfied"
        if not installed_version_id or exact_version:
            return "replace"

        installed_version = self._installed_version(installed_item)
        if installed_version is None:
            return "replace"

        compatible = self.filter_compatible_versions(
            [installed_version],
            version,
            project_type=project_type,
            game_version=game_version,
        )
        if compatible and not self._is_version_newer(candidate_version, installed_version):
            return "satisfied"
        return "replace"

    @staticmethod
    def _installed_version(item: Mapping[str, Any]) -> dict[str, Any] | None:
        version_id = item.get("modrinth_version_id")
        if not version_id:
            return None
        data = item.get("modrinth_version_data")
        if not isinstance(data, dict):
            try:
                data = ModrinthAPI.get_version_by_id(version_id)
            except Exception:
                return None
        return data if isinstance(data, dict) and data.get("id") == version_id else None

    def _append_incompatible_issues(
        self,
        incompatible_dependencies: list[tuple[dict[str, Any], tuple[str, ...]]],
        main_candidate: ModrinthInstallCandidate,
        resolved: dict[str, ModrinthInstallCandidate],
        installed_items: list[dict[str, Any]],
        issues: list[ModrinthDependencyIssue],
    ) -> None:
        planned_project_ids = {main_candidate.project_id, *resolved.keys()}
        for dependency, requested_by in incompatible_dependencies:
            project_id = str(dependency.get("project_id") or "")
            if not project_id:
                continue
            project = self._load_project(project_id)
            installed_match = self.match_installed(installed_items, project)
            if not installed_match.owned and project_id not in planned_project_ids:
                continue
            issues.append(
                self._issue_for_dependency(
                    "incompatible_installed",
                    dependency,
                    requested_by,
                    project_id=project_id,
                    message=f"{project.get('title') or project_id} conflicts with an installed or selected mod.",
                )
            )

    def _load_project(self, project_id: str) -> dict[str, Any]:
        if not project_id:
            return self._normalize_project_payload({})
        try:
            project = ModrinthAPI.get_mod(project_id)
            project["_resolved"] = True
        except Exception:
            project = {"project_id": project_id, "id": project_id, "title": project_id, "_resolved": False}
        return self._normalize_project_payload(project, fallback_project_id=project_id)

    @staticmethod
    def _normalize_project_payload(project: dict[str, Any], fallback_project_id: str = "") -> dict[str, Any]:
        normalized = dict(project or {})
        project_id = str(normalized.get("project_id") or normalized.get("id") or fallback_project_id or "")
        resolved = normalized.get("_resolved", True)
        if project_id:
            normalized["project_id"] = project_id
            normalized.setdefault("id", project_id)
        if project_id and resolved is not False and not normalized.get("slug"):
            normalized["slug"] = project_id
        if project_id and not normalized.get("title"):
            normalized["title"] = project_id
        return normalized

    @staticmethod
    def _project_page_url(project: dict[str, Any]) -> str | None:
        slug = str(project.get("slug") or "").strip()
        if not slug and project.get("_resolved") is False:
            return None
        identifier = slug or str(project.get("project_id") or project.get("id") or "").strip()
        if not identifier:
            return None
        project_type = str(project.get("project_type") or "mod").strip() or "mod"
        return f"https://modrinth.com/{project_type}/{identifier}"

    def _issue_for_dependency(
        self,
        code: str,
        dependency: dict[str, Any],
        requested_by: tuple[str, ...],
        *,
        blocking: bool = True,
        message: str | None = None,
        project_id: str | None = None,
        version_id: str | None = None,
        file_name: str | None = None,
    ) -> ModrinthDependencyIssue:
        resolved_project_id = project_id or self._optional_text(dependency.get("project_id"))
        resolved_version_id = version_id or self._optional_text(dependency.get("version_id"))
        resolved_file_name = file_name or self._optional_text(dependency.get("file_name"))
        dependency_type = str(dependency.get("dependency_type") or "")
        project = self._load_project(resolved_project_id) if resolved_project_id else {}
        return ModrinthDependencyIssue(
            code=code,
            message=message or code,
            blocking=blocking,
            project_id=resolved_project_id,
            project_title=self._optional_text(project.get("title")),
            project_slug=self._optional_text(project.get("slug")),
            project_type=self._optional_text(project.get("project_type")),
            project_url=self._project_page_url(project) if project else None,
            version_id=resolved_version_id,
            file_name=resolved_file_name,
            dependency_type=dependency_type,
            requested_by=requested_by,
        )

    @staticmethod
    def _optional_text(value: Any) -> str | None:
        text = str(value or "").strip()
        return text or None

    def _select_newest_version(self, versions: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not versions:
            return None
        return max(enumerate(versions), key=lambda item: (self._version_date(item[1]), -item[0]))[1]

    def _is_version_newer(self, candidate: dict[str, Any], current: dict[str, Any]) -> bool:
        candidate_date = self._version_date(candidate)
        current_date = self._version_date(current)
        if candidate_date != current_date:
            return candidate_date > current_date
        return str(candidate.get("id") or "") > str(current.get("id") or "")

    @staticmethod
    def _version_date(version_data: dict[str, Any]) -> datetime:
        raw_date = str(version_data.get("date_published") or "")
        if raw_date.endswith("Z"):
            raw_date = f"{raw_date[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(raw_date)
        except ValueError:
            return datetime.min.replace(tzinfo=UTC)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
