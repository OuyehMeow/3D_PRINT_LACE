"""StorageProvider implementation enforcing 'Method in the Cloud, Data at Local'.

All user inputs, intermediate drafts, session ledgers, and exported .svg / .png / .3mf
files live strictly under DATA_ROOT outside the Git repository.
"""

from __future__ import annotations

import abc
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

from ..schemas import SessionState

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_GDRIVE_FOLDER_ID = "1laThzfsycHIn_dkOsp18LKkRyTfInlc-"


def _is_inside_repo(candidate: Path) -> bool:
    """Return True if candidate is inside the Git repository tree."""
    try:
        candidate.resolve().relative_to(REPO_ROOT.resolve())
        return True
    except ValueError:
        return False


def resolve_data_root(cli_data_dir: Optional[str] = None) -> Path:
    """Resolve DATA_ROOT following precedence:
    1. Explicit CLI flag / argument (cli_data_dir)
    2. Environment variable (LACE_DATA_DIR)
    3. User-local config file (~/.config/3d_print_lace/config.json)
    4. Default external directory (/home/antigravity/lace_data_root or ~/lace_data_root)
    """
    raw_path: Optional[str] = None

    if cli_data_dir:
        raw_path = cli_data_dir
    elif os.environ.get("LACE_DATA_DIR"):
        raw_path = os.environ["LACE_DATA_DIR"]
    else:
        cfg_file = Path.home() / ".config" / "3d_print_lace" / "config.json"
        if cfg_file.exists():
            try:
                data = json.loads(cfg_file.read_text(encoding="utf-8"))
                raw_path = data.get("data_dir") or data.get("storage", {}).get("data_dir")
            except Exception:
                raw_path = None

    if raw_path:
        resolved = Path(raw_path).expanduser().resolve()
    else:
        resolved = (Path.home() / "lace_data_root").resolve()

    if _is_inside_repo(resolved):
        raise ValueError(
            f"DATA_ROOT ({resolved}) must reside outside the Git repository ({REPO_ROOT})."
        )

    return resolved


class StorageProvider(abc.ABC):
    """Abstract interface for external data I/O."""

    @property
    @abc.abstractmethod
    def data_root(self) -> Path:
        """Root directory for all external data."""

    @abc.abstractmethod
    def save_input_image(self, filename: str, content: bytes) -> Path:
        """Save an uploaded input image to DATA_ROOT/inputs/."""

    @abc.abstractmethod
    def get_input_image_path(self, filename: str) -> Path:
        """Return absolute path to an input image in DATA_ROOT/inputs/."""

    @abc.abstractmethod
    def save_session(self, session: SessionState) -> Path:
        """Persist session state ledger to DATA_ROOT/sessions/<session_id>/session.json."""

    @abc.abstractmethod
    def load_session(self, session_id: str) -> SessionState:
        """Load session state from DATA_ROOT/sessions/<session_id>/session.json."""

    @abc.abstractmethod
    def list_sessions(self) -> list[dict[str, Any]]:
        """List available sessions in DATA_ROOT/sessions/."""

    @abc.abstractmethod
    def save_export(self, session_id: str, filename: str, content: bytes | str) -> Path:
        """Save an exported .svg, .png, or .3mf file to DATA_ROOT/sessions/<id>/exports/."""

    @abc.abstractmethod
    def get_export_path(self, session_id: str, filename: str) -> Path:
        """Get absolute path to an exported file."""


class LocalStorageProvider(StorageProvider):
    """Primary local filesystem storage provider backed by DATA_ROOT."""

    def __init__(self, data_dir: Optional[str | Path] = None) -> None:
        self._root = resolve_data_root(str(data_dir) if data_dir else None)
        self._inputs_dir = self._root / "inputs"
        self._sessions_dir = self._root / "sessions"
        self._cache_dir = self._root / "cache"
        for d in (self._inputs_dir, self._sessions_dir, self._cache_dir):
            d.mkdir(parents=True, exist_ok=True)

    @property
    def data_root(self) -> Path:
        return self._root

    def _safe_name(self, filename: str) -> str:
        return Path(filename).name

    def save_input_image(self, filename: str, content: bytes) -> Path:
        safe = self._safe_name(filename)
        target = self._inputs_dir / safe
        target.write_bytes(content)
        return target

    def get_input_image_path(self, filename: str) -> Path:
        safe = self._safe_name(filename)
        target = self._inputs_dir / safe
        if not target.exists():
            raise FileNotFoundError(f"Input image not found in DATA_ROOT: {target}")
        return target

    def _session_dir(self, session_id: str) -> Path:
        safe_id = self._safe_name(session_id)
        sdir = self._sessions_dir / safe_id
        (sdir / "drafts").mkdir(parents=True, exist_ok=True)
        (sdir / "exports").mkdir(parents=True, exist_ok=True)
        return sdir

    def save_session(self, session: SessionState) -> Path:
        sdir = self._session_dir(session.session_id)
        ledger = sdir / "session.json"
        ledger.write_text(session.model_dump_json(indent=2), encoding="utf-8")
        return ledger

    def load_session(self, session_id: str) -> SessionState:
        sdir = self._sessions_dir / self._safe_name(session_id)
        ledger = sdir / "session.json"
        if not ledger.exists():
            raise FileNotFoundError(f"Session {session_id} not found at {ledger}")
        return SessionState.model_validate_json(ledger.read_text(encoding="utf-8"))

    def list_sessions(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        if not self._sessions_dir.exists():
            return results
        for child in sorted(self._sessions_dir.iterdir()):
            ledger = child / "session.json"
            if ledger.exists():
                try:
                    state = SessionState.model_validate_json(
                        ledger.read_text(encoding="utf-8")
                    )
                    results.append(
                        {
                            "session_id": state.session_id,
                            "name": state.name,
                            "source_image_filename": state.source_image_filename,
                            "style_draft_count": len(state.style_drafts),
                            "color_draft_count": len(state.color_drafts),
                            "grid_draft_count": len(state.grid_drafts),
                        }
                    )
                except Exception:
                    continue
        return results

    def save_export(self, session_id: str, filename: str, content: bytes | str) -> Path:
        sdir = self._session_dir(session_id)
        target = sdir / "exports" / self._safe_name(filename)
        if isinstance(content, str):
            target.write_text(content, encoding="utf-8")
        else:
            target.write_bytes(content)
        return target

    def get_export_path(self, session_id: str, filename: str) -> Path:
        sdir = self._sessions_dir / self._safe_name(session_id)
        target = sdir / "exports" / self._safe_name(filename)
        if not target.exists():
            raise FileNotFoundError(f"Export not found: {target}")
        return target


class GDriveStorageProvider(LocalStorageProvider):
    """Cloud storage adapter that mirrors local DATA_ROOT operations with Google Drive."""

    def __init__(
        self,
        data_dir: Optional[str | Path] = None,
        folder_id: str = DEFAULT_GDRIVE_FOLDER_ID,
    ) -> None:
        super().__init__(data_dir=data_dir)
        self.folder_id = folder_id

    def list_drive_files(self, max_items: int = 25) -> list[dict[str, Any]]:
        """List files in the connected 3D_PRINT_LACE Google Drive folder (readonly)."""
        gdrive_bin = shutil.which("gdrive")
        if not gdrive_bin:
            return []
        try:
            proc = subprocess.run(
                [
                    gdrive_bin,
                    "readonly",
                    "ls",
                    self.folder_id,
                    "--max",
                    str(max_items),
                    "--json",
                ],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                data = json.loads(proc.stdout)
                if isinstance(data, list):
                    return data
                return data.get("files", [])
        except Exception:
            pass
        return []

    def download_drive_input(self, file_id: str, filename: str) -> Path:
        """Download a file from Google Drive into DATA_ROOT/inputs/."""
        gdrive_bin = shutil.which("gdrive")
        if not gdrive_bin:
            raise RuntimeError("gdrive CLI is not available on PATH")
        target = self._inputs_dir / self._safe_name(filename)
        subprocess.run(
            [gdrive_bin, "readonly", "download", file_id, "--out", str(target)],
            check=True,
            timeout=30,
        )
        return target


_PROVIDER_INSTANCE: Optional[LocalStorageProvider] = None


def get_storage_provider(data_dir: Optional[str] = None) -> LocalStorageProvider:
    """Return singleton LocalStorageProvider resolved against DATA_ROOT."""
    global _PROVIDER_INSTANCE
    if data_dir is not None:
        return LocalStorageProvider(data_dir=data_dir)
    if _PROVIDER_INSTANCE is None:
        _PROVIDER_INSTANCE = LocalStorageProvider()
    return _PROVIDER_INSTANCE
