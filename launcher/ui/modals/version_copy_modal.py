from __future__ import annotations

import os
import shutil
from pathlib import Path

import flet as ft

from launcher.application.version_snapshot import mark_manual_copy_options, write_copy_snapshot
from launcher.core import Launcher, util
from launcher.core.versions import Version

from ..controls.button import Button
from ..controls.text import Text
from ..core.page_runtime import close_dialog, run_blocking, run_task, schedule_update, show_dialog
from ..feedback.alert_dialog import AlertDialog
from ..forms.field_specs import FieldSpec, build_field
from ..layout.column import Column


class VersionCopyModal:
    def __init__(self, app, source_version: Version) -> None:
        self.app = app
        self.page = app.page
        self.source_version = source_version
        self.content_width = self.app.theme.modal_width
        self.version_name = build_field(
            self.app,
            FieldSpec(
                type="textfield",
                key="version_name",
                label=self.app.trans("enter_name"),
                value=f"{source_version.name} (Copy)",
                width=self.content_width,
            ),
            on_change=lambda _e: None,
        )
        is_tensacraft = "tensacraft" in (source_version.client or "").lower() or "tensa" in (source_version.client or "").lower()
        default_copy_client = self._infer_loader_type(source_version) if is_tensacraft else source_version.client
        self.type_select = build_field(
            self.app,
            FieldSpec(
                type="dropdown",
                key="copy_client",
                label=self.app.trans("select_client"),
                value=default_copy_client or "minecraft",
                options=self._build_loader_options(),
                width=self.content_width,
                props={"disabled": not is_tensacraft},
            ),
            on_change=lambda _e: None,
        )
        controls = [
            self.version_name,
            Text(
                self.app.trans("copy_version_info", version=source_version.name, client=source_version.client, mc_version=source_version.version),
                size=self.app.theme.text_size_sm,
                color=self.app.theme.text_secondary,
            ),
        ]
        if is_tensacraft:
            controls.append(self.type_select)
        self.modal = AlertDialog(
            title=Text(self.app.trans("copy_version_title"), color=self.app.theme.text_color),
            modal=True,
            content=Column(
                controls,
                width=self.content_width,
                height=self.app.theme.modal_height,
                spacing=self.app.theme.spacing_md,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            ),
            actions=[
                Button(text=self.app.trans("copy"), on_click=self.copy_version),
                Button(text=self.app.trans("close"), variant="outline", tone="neutral", on_click=lambda _e: self.close()),
            ],
        )

    @staticmethod
    def _build_loader_options() -> list[dict]:
        seen = set()
        options = []
        for loader in Launcher().loaders():
            loader_id = loader.get_id()
            if loader_id in {"modrinth", "tensacraft"} or loader_id in seen:
                continue
            seen.add(loader_id)
            options.append({"text": loader.get_name(), "key": loader_id})
        return options

    @staticmethod
    def _infer_loader_type(version: Version) -> str:
        loader = str(getattr(version, "loader", "") or "").lower()
        if loader.startswith("fabric-loader-"):
            return "fabric"
        if loader.startswith("quilt-loader-"):
            return "quilt"
        if loader.startswith("neoforge-"):
            return "neoforge"
        if "-forge-" in loader or loader.startswith("forge-"):
            return "forge"

        client = str(getattr(version, "client", "") or "").lower()
        if client in {"minecraft", "fabric", "forge", "neoforge", "quilt"}:
            return client
        return "minecraft"

    @staticmethod
    def _client_display_name(loader_type: str) -> str:
        names = {
            "minecraft": "Minecraft",
            "fabric": "Fabric",
            "forge": "Forge",
            "neoforge": "NeoForge",
            "quilt": "Quilt",
        }
        return names.get(loader_type, loader_type.capitalize())

    def _loader_id_for_copy(self, loader_type: str) -> str:
        source_loader_type = self._infer_loader_type(self.source_version)
        source_loader = str(getattr(self.source_version, "loader", "") or "")
        if loader_type == source_loader_type and source_loader:
            return source_loader

        loader_version = getattr(self.source_version, "loader_version", None)
        minecraft_version = getattr(self.source_version, "version", None)
        if loader_type == "fabric" and loader_version and minecraft_version:
            return f"fabric-loader-{loader_version}-{minecraft_version}"
        if loader_type == "quilt" and loader_version and minecraft_version:
            return f"quilt-loader-{loader_version}-{minecraft_version}"
        if loader_type == "neoforge" and loader_version:
            return f"neoforge-{loader_version}"
        if loader_type == "forge" and loader_version and minecraft_version:
            return f"{minecraft_version}-forge-{loader_version}"
        if loader_type == "minecraft" and minecraft_version:
            return str(minecraft_version)
        return loader_type

    def _minecraft_dir(self) -> Path:
        paths = getattr(self.app, "paths", None)
        if paths is not None:
            minecraft_dir = getattr(paths, "minecraft_dir", None)
            if minecraft_dir:
                return Path(minecraft_dir)
        app_util = getattr(self.app, "util", None)
        if app_util is not None:
            minecraft_dir = getattr(app_util, "minecraft_dir", None)
            if minecraft_dir:
                return Path(minecraft_dir)
        return Path(util.minecraft_dir)

    def _games_dir(self) -> Path:
        paths = getattr(self.app, "paths", None)
        if paths is not None:
            games_dir = getattr(paths, "games_dir", None)
            if games_dir:
                return Path(games_dir)
        app_util = getattr(self.app, "util", None)
        if app_util is not None:
            games_path = getattr(app_util, "games_path", None)
            if games_path:
                return Path(games_path)
        return Path(util.games_path)

    def _resolve_existing_path(self, raw_path: str | os.PathLike[str] | None) -> Path:
        if not raw_path:
            raise FileNotFoundError("Source version path is missing")

        source_path = Path(raw_path)
        if source_path.is_absolute():
            return source_path

        candidates = [
            self._minecraft_dir() / source_path,
            self._games_dir() / source_path,
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return candidates[0]

    def copy_version(self, _):
        name = self.version_name.value.strip()
        if not name:
            self.app.feedback.info(self.app.trans("fill_all_fields"))
            return
        if self.app.versions.get_by_name(name):
            self.app.feedback.info(self.app.trans("version_exists", name=name))
            return
        if self.app.feedback.is_busy():
            self.app.feedback.info(self.app.trans("installation_already_running"))
            return
        self.close()
        operation = self.app.feedback.begin_operation(
            self.app.trans("copy_in_progress"),
            kind="copy",
            status=self.app.trans("copy_in_progress"),
        )
        try:
            run_task(self.page, self._copy_version_async, name, operation)
        except Exception:
            operation.fail(
                self.app.trans("version_copy_error", version=self.source_version.name, error="run_task failed"),
                notify=False,
            )
            raise
        return None

    async def _copy_version_async(self, name: str, operation):
        final_message = self.app.trans("copy_complete")
        finish_level = "success"
        copied = False
        try:
            await run_blocking(self._copy_version_impl, name)
            copied = True
        except Exception as exc:
            self.app.log.error(f"Failed to copy version {self.source_version.name}: {exc}")
            final_message = self.app.trans("version_copy_error", version=self.source_version.name, error=str(exc))
            finish_level = "warning"
            self.app.feedback.warning(final_message)
        finally:
            operation.finish(final_message, show_success=False, level=finish_level)
        if copied:
            await self.app.feedback.wait_until_progress_hidden()
            self.app.feedback.info(self.app.trans("version_copy_success", version=name))
            self.app.show_versions_page()

    def _copy_version_impl(self, name: str) -> None:
        is_tensacraft = "tensacraft" in (self.source_version.client or "").lower() or "tensa" in (self.source_version.client or "").lower()
        new_client = self.type_select.value if is_tensacraft else self.source_version.client
        if is_tensacraft:
            loader_type = str(new_client or self._infer_loader_type(self.source_version)).lower()
            new_loader = self._loader_id_for_copy(loader_type)
            new_client = self._client_display_name(loader_type)
        else:
            new_loader = self.source_version.loader if getattr(self.source_version, "loader", None) else new_client
        new_version_id = util.normalize_string(name)
        dest_path = self._games_dir() / new_version_id
        options = self.source_version.options.copy()
        if is_tensacraft:
            options = mark_manual_copy_options(options, source_version_id=self.source_version.version_id)
        new_data = {
            "name": name,
            "version": self.source_version.version,
            "client": new_client,
            "loader": new_loader,
            "loader_version": self.source_version.loader_version,
            "force_update": False if is_tensacraft else self.source_version.force_update,
            "options": options,
            "image": self.source_version.image,
            "path": str(dest_path),
        }
        source_path = self._resolve_existing_path(self.source_version.path)
        if not source_path.exists():
            raise FileNotFoundError(f"Source version directory was not found: {source_path}")
        if dest_path.exists():
            shutil.rmtree(dest_path)
        shutil.copytree(source_path, dest_path)
        copied_version = Version(new_version_id, new_data)
        if is_tensacraft:
            write_copy_snapshot(self.source_version, copied_version, dest_path)
        copied_version.save()

    def show(self):
        if self.app.feedback.is_busy():
            self.app.feedback.info(self.app.trans("installation_already_running"))
            return
        show_dialog(self.page, self.modal)
        schedule_update(self.page)

    def close(self):
        close_dialog(self.page, self.modal)
        schedule_update(self.page)


__all__ = ["VersionCopyModal"]
