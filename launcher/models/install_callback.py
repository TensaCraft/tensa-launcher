from __future__ import annotations

from launcher.application.feedback import OperationHandle


class InstallCallback:
    def __init__(self, app, operation: OperationHandle | None = None):
        self.app = app
        self.operation = operation
        self.current_max = 0

    def _update_operation(
        self,
        status: str | None = None,
        *,
        progress: float | None = None,
        total: float | None = None,
    ) -> None:
        if self.operation is not None:
            self.operation.update(status, progress=progress, total=total)

    def set_max(self, new_max: int):
        self.current_max = new_max

    def set_status(self, new_status: str):
        translations = {
            "Installation complete": self.app.trans("installation_complete"),
            "Download Assets": self.app.trans("download_assets"),
            "Download": self.app.trans("download"),
            "Install": self.app.trans("install"),
            "Running": self.app.trans("running"),
            "Installing": self.app.trans("installing")
        }
        for eng, ukr in translations.items():
            new_status = new_status.replace(eng, ukr)
        self._update_operation(new_status)

    def set_progress(self, progress: int):
        if self.current_max != 0:
            progress_percent = (progress / self.current_max) * 100
            self._update_operation(progress=progress_percent, total=100)

    def handle_installation_progress(self, action, value, max_value=None):
        if action == "setStatus":
            self.set_status(value)
        elif action == "setProgress":
            if max_value is not None:
                self.set_max(max_value)
            self.set_progress(value)
        elif action == "setMax":
            self.set_max(value)

    def get_install_callbacks(self):
        return {
            "setStatus": lambda value: self.handle_installation_progress("setStatus", value),
            "setProgress": lambda value, max_value=None: self.handle_installation_progress("setProgress", value, max_value),
            "setMax": lambda value: self.handle_installation_progress("setMax", value)
        }
