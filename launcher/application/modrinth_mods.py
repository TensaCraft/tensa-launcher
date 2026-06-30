from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from launcher.core.api.modrinth import ModrinthAPI


@dataclass(slots=True)
class ModInstallFile:
    url: str
    filename: str
    version_number: str = ""


@dataclass(slots=True)
class ModrinthInstallCandidate:
    project: dict[str, Any]
    version_data: dict[str, Any]
    install_file: ModInstallFile
    action: str
    dependency_type: str = "selected"
    requested_by: tuple[str, ...] = ()
    installed_item: dict[str, Any] | None = None

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
        unique_optional = [candidate for candidate in selected if candidate.project_id not in required_ids]
        return [*self.dependencies_to_replace, *self.dependencies_to_install, *unique_optional, self.main]


class ModrinthModsService:
    LOADER_SCOPED_PROJECT_TYPES = {"mod"}
    FABRIC_API_PROJECT_ID = "P7dR8mSH"

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
        installed = installed_items or []
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

    def find_update(self, installed_mod: dict[str, Any], version) -> dict[str, Any] | None:
        mod_id = installed_mod.get("id")
        if not mod_id or not installed_mod.get("enabled", True):
            return None

        latest_version = self.find_latest_version(mod_id, version)
        if latest_version is None:
            return None

        current_version = installed_mod.get("version", "")
        latest_version_number = latest_version.get("version_number", "")
        if current_version and latest_version_number and current_version != latest_version_number:
            return latest_version
        return None

    @staticmethod
    def select_primary_file(version_data: dict[str, Any] | None) -> ModInstallFile | None:
        if not version_data:
            return None

        files = version_data.get("files", [])
        if not files:
            return None

        mod_file = next((file for file in files if file.get("primary", False)), files[0])
        return ModInstallFile(
            url=mod_file["url"],
            filename=mod_file["filename"],
            version_number=version_data.get("version_number", ""),
        )

    @staticmethod
    def is_installed(installed_mods: list[dict[str, Any]], project: dict[str, Any]) -> bool:
        return ModrinthModsService.find_installed(installed_mods, project) is not None

    @staticmethod
    def find_installed(installed_mods: list[dict[str, Any]], project: dict[str, Any]) -> dict[str, Any] | None:
        project_id = project.get("project_id")
        project_slug = ModrinthModsService._normalize_identifier(project.get("slug"))
        project_name = ModrinthModsService._normalize_identifier(project.get("title"))
        candidates = {value for value in (project_slug, project_name) if value}

        for mod in installed_mods:
            if project_id and project_id in {mod.get("id"), mod.get("modrinth_project_id")}:
                return mod

            installed_values = {
                ModrinthModsService._normalize_identifier(mod.get("name")),
                ModrinthModsService._normalize_identifier(mod.get("filename")),
            }
            for installed_value in installed_values:
                if not installed_value:
                    continue
                if any(
                    installed_value == candidate or installed_value.startswith(f"{candidate}-")
                    for candidate in candidates
                ):
                    return mod

        return None

    @staticmethod
    def _normalize_identifier(value: Any) -> str:
        text = str(value or "").strip().lower()
        if text.endswith(".disabled"):
            text = text[:-9]
        for suffix in (".jar", ".zip"):
            if text.endswith(suffix):
                text = text[: -len(suffix)]
        return "-".join(part for part in text.replace("_", "-").split() if part)

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
    ) -> ModrinthDependencyPlan:
        resolved: dict[str, ModrinthInstallCandidate] = {}
        optional: dict[str, ModrinthInstallCandidate] = {}
        optional_satisfied: list[ModrinthInstallCandidate] = []
        optional_issues: list[ModrinthDependencyIssue] = []
        embedded: list[ModrinthDependencyIssue] = []
        issues: list[ModrinthDependencyIssue] = []
        incompatible_dependencies: list[tuple[dict[str, Any], tuple[str, ...]]] = []
        processed_versions: set[tuple[str, str]] = {(main_candidate.project_id, main_candidate.version_id)}
        queue: list[tuple[ModrinthInstallCandidate, dict[str, Any]]] = [
            (main_candidate, dependency)
            for dependency in main_candidate.version_data.get("dependencies", [])
            if isinstance(dependency, dict)
        ]

        while queue:
            source, dependency = queue.pop(0)
            dependency_type = str(dependency.get("dependency_type") or "required").lower()
            requested_by = (*source.requested_by, source.title)

            if dependency_type == "optional":
                candidate = self._resolve_dependency_candidate(
                    dependency,
                    version,
                    project_type=project_type,
                    game_version=game_version,
                    installed_items=installed_items,
                    requested_by=requested_by,
                    issues=optional_issues,
                    dependency_kind="optional",
                    blocking=False,
                )
                if candidate is None or candidate.project_id == main_candidate.project_id:
                    continue
                if candidate.action == "satisfied":
                    optional_satisfied.append(candidate)
                else:
                    existing_optional = optional.get(candidate.project_id)
                    if existing_optional is None or self._is_version_newer(candidate.version_data, existing_optional.version_data):
                        optional[candidate.project_id] = candidate
                continue
            if dependency_type == "embedded":
                embedded.append(self._issue_for_dependency("embedded_dependency", dependency, requested_by, blocking=False))
                continue
            if dependency_type == "incompatible":
                incompatible_dependencies.append((dependency, requested_by))
                continue
            if dependency_type != "required":
                optional_issues.append(
                    self._issue_for_dependency("unsupported_dependency_type", dependency, requested_by, blocking=False)
                )
                continue

            candidate = self._resolve_dependency_candidate(
                dependency,
                version,
                project_type=project_type,
                game_version=game_version,
                installed_items=installed_items,
                requested_by=requested_by,
                issues=issues,
                dependency_kind="required",
                blocking=True,
            )
            if candidate is None or candidate.project_id == main_candidate.project_id:
                continue

            existing = resolved.get(candidate.project_id)
            if existing is None or self._is_version_newer(candidate.version_data, existing.version_data):
                resolved[candidate.project_id] = candidate
                candidate_for_traversal = candidate
            else:
                candidate_for_traversal = existing

            version_key = (candidate_for_traversal.project_id, candidate_for_traversal.version_id)
            if version_key in processed_versions:
                continue
            processed_versions.add(version_key)
            for child_dependency in candidate_for_traversal.version_data.get("dependencies", []):
                if isinstance(child_dependency, dict):
                    queue.append((candidate_for_traversal, child_dependency))

        if include_implicit_dependencies:
            self._append_implicit_dependencies(
                main_candidate,
                resolved,
                version,
                project_type=project_type,
                game_version=game_version,
                installed_items=installed_items,
                issues=issues,
            )

        self._append_incompatible_issues(
            incompatible_dependencies,
            main_candidate,
            resolved,
            installed_items,
            issues,
        )

        candidates = list(resolved.values())
        return ModrinthDependencyPlan(
            main=main_candidate,
            dependencies_to_install=[candidate for candidate in candidates if candidate.action == "install"],
            dependencies_to_replace=[candidate for candidate in candidates if candidate.action == "replace"],
            already_satisfied=[candidate for candidate in candidates if candidate.action == "satisfied"] + optional_satisfied,
            optional_dependencies=list(optional.values()),
            skipped_embedded=embedded,
            blocking_issues=issues,
            optional_dependency_issues=optional_issues,
        )

    def _append_implicit_dependencies(
        self,
        main_candidate: ModrinthInstallCandidate,
        resolved: dict[str, ModrinthInstallCandidate],
        version,
        *,
        project_type: str,
        game_version: str | None,
        installed_items: list[dict[str, Any]],
        issues: list[ModrinthDependencyIssue],
    ) -> None:
        if project_type != "mod" or self.get_loader_name(version) != "fabric":
            return
        if main_candidate.project_id == self.FABRIC_API_PROJECT_ID:
            return
        if self.FABRIC_API_PROJECT_ID in resolved:
            return

        candidate = self._resolve_dependency_candidate(
            {"project_id": self.FABRIC_API_PROJECT_ID, "dependency_type": "required"},
            version,
            project_type=project_type,
            game_version=game_version,
            installed_items=installed_items,
            requested_by=(main_candidate.title,),
            issues=issues,
            dependency_kind="required",
            blocking=True,
        )
        if candidate is None or candidate.project_id == main_candidate.project_id:
            return
        resolved[candidate.project_id] = candidate

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
            if exact_version is not None:
                project_id = project_id or str(exact_version.get("project_id") or "")
                if self.filter_compatible_versions(
                    [exact_version],
                    version,
                    project_type=project_type,
                    game_version=game_version,
                ):
                    version_data = exact_version
            if version_data is None and project_id:
                version_data = self._select_newest_version(
                    self._get_compatible_versions_safely(
                        project_id,
                        version,
                        project_type=project_type,
                        game_version=game_version,
                    )
                )
            if version_data is None:
                code = "dependency_resolution_failed" if exact_version is None else "dependency_incompatible"
                issues.append(
                    self._issue_for_dependency(
                        code,
                        dependency,
                        requested_by,
                        blocking=blocking,
                        project_id=project_id or None,
                        version_id=version_id,
                    )
                )
                return None
        elif project_id:
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

        project = self._load_project(project_id or str(version_data.get("project_id") or ""))
        return self._candidate_for(
            project,
            version_data,
            install_file,
            installed_items,
            version,
            project_type=project_type,
            game_version=game_version,
            dependency_type=dependency_kind,
            requested_by=requested_by,
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
    ) -> ModrinthInstallCandidate:
        normalized_project = self._normalize_project_payload(
            project,
            fallback_project_id=str(version_data.get("project_id") or ""),
        )
        installed_item = self.find_installed(installed_items, normalized_project)
        action = self._candidate_action(
            installed_item,
            version_data,
            version,
            project_type=project_type,
            game_version=game_version,
        )
        return ModrinthInstallCandidate(
            project=normalized_project,
            version_data=version_data,
            install_file=install_file,
            action=action,
            dependency_type=dependency_type,
            requested_by=requested_by,
            installed_item=installed_item,
        )

    def _candidate_action(
        self,
        installed_item: dict[str, Any] | None,
        candidate_version: dict[str, Any],
        version,
        *,
        project_type: str,
        game_version: str | None,
    ) -> str:
        if installed_item is None:
            return "install"
        if not installed_item.get("enabled", True):
            return "replace"

        installed_version_id = str(installed_item.get("modrinth_version_id") or "")
        candidate_version_id = str(candidate_version.get("id") or "")
        if installed_version_id and installed_version_id == candidate_version_id:
            return "satisfied"
        if not installed_version_id:
            return "replace"

        try:
            installed_version = ModrinthAPI.get_version_by_id(installed_version_id)
        except Exception:
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
            installed_item = self.find_installed(installed_items, project)
            if installed_item is None and project_id not in planned_project_ids:
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
