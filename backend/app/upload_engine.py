from __future__ import annotations

from typing import Protocol


class UploadEngineClient(Protocol):
    def status(self) -> dict[str, str]: ...

    async def upload_file(
        self,
        channel_target: str,
        file_paths: list[str],
        caption: str,
        progress_callback,
        force_document: bool = False,
    ): ...

    async def shutdown(self) -> None: ...
