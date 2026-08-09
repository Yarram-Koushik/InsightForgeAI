"""
InsightForgeAI – Durable Workspace Store (Phase 3.3)

Persists datasets, chat history, evidence packs and metric catalog so that
restarting the app no longer loses state.

Layout (under data/workspaces/{workspace_id}/):

  meta.json                 – workspace-level metadata + dataset registry
  datasets/{name}/
      meta.json             – DatasetRecord metadata, issues, lineage
      cleaned.parquet       – cleaned DataFrame (required)
      raw.parquet           – optional raw DataFrame
  chat/
      history.jsonl         – one JSON object per turn (append-only)
  catalog/                  – metric governance overrides (reuses Phase 3.5 format)

Design principles
-----------------
- Fail closed on corrupt files (skip + warn, never crash the app)
- Retention policy for chat (default keep last 100 turns)
- Dataset name collision → new id + lineage note
- Free-stack: pandas + pyarrow (already in deps) + stdlib json/pathlib
- Backward compatible: existing Workspace API still works; this is a persistence layer
"""

from __future__ import annotations

import json
import shutil
import uuid
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def _project_root() -> Path:
    here = Path(__file__).resolve()
    # app/core/workspace_store.py → project root
    for p in [here.parents[2], Path.cwd()]:
        if (p / "app").exists() or (p / "data").exists():
            return p
    return Path.cwd()


def default_workspaces_root() -> Path:
    root = _project_root() / "data" / "workspaces"
    root.mkdir(parents=True, exist_ok=True)
    return root


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _safe_name(name: str) -> str:
    import re
    return re.sub(r"[^a-zA-Z0-9_\-]+", "_", (name or "unnamed").strip()) or "unnamed"


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _df_to_parquet(df: pd.DataFrame, path: Path) -> None:
    """Prefer parquet; fall back to CSV if pyarrow/fastparquet missing."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        df.to_parquet(path, index=False)
    except (ImportError, ValueError, Exception):
        # Fallback for constrained environments
        csv_path = path.with_suffix(".csv")
        df.to_csv(csv_path, index=False)
        # Also write a marker so loader knows
        path.write_text("FALLBACK_CSV", encoding="utf-8")


def _df_from_parquet(path: Path) -> Optional[pd.DataFrame]:
    csv_path = path.with_suffix(".csv")
    if not path.exists() and not csv_path.exists():
        return None

    # Marker file written by CSV fallback
    if path.exists():
        try:
            # Only try text read on tiny files (the marker is ~12 bytes)
            if path.stat().st_size < 64:
                content = path.read_text(encoding="utf-8").strip()
                if content == "FALLBACK_CSV" and csv_path.exists():
                    return pd.read_csv(csv_path)
        except Exception:
            pass
        # Real parquet
        try:
            return pd.read_parquet(path)
        except Exception:
            pass

    if csv_path.exists():
        try:
            return pd.read_csv(csv_path)
        except Exception:
            return None
    return None


# ---------------------------------------------------------------------------
# Chat turn model (lightweight, serializable)
# ---------------------------------------------------------------------------

@dataclass
class ChatTurn:
    id: str
    question: str
    success: bool = True
    intent: Optional[str] = None
    intent_reason: Optional[str] = None
    message: Optional[str] = None
    sql: Optional[str] = None
    insight: Optional[str] = None
    clarify_questions: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    error: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    steps: List[str] = field(default_factory=list)
    table_name: Optional[str] = None
    created_at: str = field(default_factory=_now_iso)
    # Evidence is stored separately or as a compact dict to avoid huge files
    evidence_summary: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ChatTurn":
        return cls(
            id=str(d.get("id") or str(uuid.uuid4())[:8]),
            question=str(d.get("question") or ""),
            success=bool(d.get("success", True)),
            intent=d.get("intent"),
            intent_reason=d.get("intent_reason"),
            message=d.get("message"),
            sql=d.get("sql"),
            insight=d.get("insight"),
            clarify_questions=list(d.get("clarify_questions") or []),
            warnings=list(d.get("warnings") or []),
            error=d.get("error"),
            provider=d.get("provider"),
            model=d.get("model"),
            steps=list(d.get("steps") or []),
            table_name=d.get("table_name"),
            created_at=str(d.get("created_at") or _now_iso()),
            evidence_summary=d.get("evidence_summary"),
        )


# ---------------------------------------------------------------------------
# Workspace Store
# ---------------------------------------------------------------------------

class WorkspaceStore:
    """
    Durable persistence for a single workspace.

    Usage:
        store = WorkspaceStore(workspace_id="default")
        store.save_dataset(record)
        store.append_chat_turn(turn)
        store.load_into(workspace)   # restores datasets + registers in DuckDB
    """

    def __init__(
        self,
        workspace_id: str = "default",
        root: Optional[Path] = None,
        max_chat_turns: int = 100,
    ):
        self.workspace_id = _safe_name(workspace_id)
        self.root = (root or default_workspaces_root()) / self.workspace_id
        self.max_chat_turns = max(5, int(max_chat_turns))
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "datasets").mkdir(exist_ok=True)
        (self.root / "chat").mkdir(exist_ok=True)
        (self.root / "catalog").mkdir(exist_ok=True)

    # ---- paths ----
    @property
    def meta_path(self) -> Path:
        return self.root / "meta.json"

    @property
    def chat_path(self) -> Path:
        return self.root / "chat" / "history.jsonl"

    def dataset_dir(self, name: str) -> Path:
        return self.root / "datasets" / _safe_name(name)

    # ---- meta ----
    def load_meta(self) -> Dict[str, Any]:
        meta = _read_json(self.meta_path, default={})
        if not isinstance(meta, dict):
            meta = {}
        meta.setdefault("workspace_id", self.workspace_id)
        meta.setdefault("created_at", _now_iso())
        meta.setdefault("updated_at", _now_iso())
        meta.setdefault("datasets", [])
        meta.setdefault("version", 1)
        return meta

    def save_meta(self, meta: Optional[Dict[str, Any]] = None) -> None:
        meta = meta or self.load_meta()
        meta["workspace_id"] = self.workspace_id
        meta["updated_at"] = _now_iso()
        _write_json(self.meta_path, meta)

    # ---- datasets ----
    def save_dataset(self, record: Any, include_raw: bool = False) -> Path:
        """
        Persist a DatasetRecord.
        `record` must have: name, id, source_filename, created_at,
        cleaned_df, metadata, issues, lineage, and optionally raw_df.
        """
        ddir = self.dataset_dir(record.name)
        ddir.mkdir(parents=True, exist_ok=True)

        meta = {
            "id": getattr(record, "id", None),
            "name": record.name,
            "source_filename": getattr(record, "source_filename", None),
            "created_at": getattr(record, "created_at", _now_iso()),
            "metadata": dict(getattr(record, "metadata", {}) or {}),
            "issues": list(getattr(record, "issues", []) or []),
            "lineage": list(getattr(record, "lineage", []) or []),
            "saved_at": _now_iso(),
        }
        _write_json(ddir / "meta.json", meta)

        cleaned = getattr(record, "cleaned_df", None)
        if cleaned is not None and isinstance(cleaned, pd.DataFrame):
            _df_to_parquet(cleaned, ddir / "cleaned.parquet")

        if include_raw:
            raw = getattr(record, "raw_df", None)
            if raw is not None and isinstance(raw, pd.DataFrame):
                _df_to_parquet(raw, ddir / "raw.parquet")

        # Update workspace meta registry
        wmeta = self.load_meta()
        names = [d.get("name") for d in wmeta.get("datasets", [])]
        entry = {
            "name": record.name,
            "id": meta["id"],
            "source_filename": meta["source_filename"],
            "rows": int(meta["metadata"].get("cleaned_rows") or meta["metadata"].get("original_rows") or 0),
            "saved_at": meta["saved_at"],
        }
        if record.name in names:
            wmeta["datasets"] = [entry if d.get("name") == record.name else d for d in wmeta["datasets"]]
        else:
            wmeta["datasets"].append(entry)
        self.save_meta(wmeta)
        return ddir

    def list_saved_datasets(self) -> List[str]:
        meta = self.load_meta()
        return [d["name"] for d in meta.get("datasets", []) if d.get("name")]

    def load_dataset_record(self, name: str, workspace_cls: Any = None) -> Optional[Any]:
        """
        Reconstruct a DatasetRecord-like object from disk.
        If workspace_cls / DatasetRecord is provided we use it; otherwise return a simple namespace.
        """
        ddir = self.dataset_dir(name)
        meta = _read_json(ddir / "meta.json")
        if not meta:
            return None

        cleaned = _df_from_parquet(ddir / "cleaned.parquet")
        if cleaned is None:
            return None

        raw = _df_from_parquet(ddir / "raw.parquet")
        if raw is None:
            raw = cleaned.copy()

        # Prefer real DatasetRecord if available
        try:
            from app.core.data_manager import DatasetRecord
            record = DatasetRecord(
                name=meta.get("name") or name,
                raw_df=raw,
                source_filename=meta.get("source_filename") or "restored",
            )
            record.id = meta.get("id") or record.id
            record.created_at = meta.get("created_at") or record.created_at
            record.cleaned_df = cleaned
            record.metadata = dict(meta.get("metadata") or {})
            record.issues = list(meta.get("issues") or [])
            record.lineage = list(meta.get("lineage") or [])
            # Mark that it came from durable store
            record.metadata["restored_from"] = str(ddir)
            record.metadata["duckdb_registered"] = False
            return record
        except Exception:
            # Fallback plain object
            class _Rec:
                pass
            r = _Rec()
            r.id = meta.get("id")
            r.name = meta.get("name") or name
            r.source_filename = meta.get("source_filename")
            r.created_at = meta.get("created_at")
            r.raw_df = raw
            r.cleaned_df = cleaned
            r.metadata = dict(meta.get("metadata") or {})
            r.issues = list(meta.get("issues") or [])
            r.lineage = list(meta.get("lineage") or [])
            return r

    def delete_dataset(self, name: str) -> bool:
        ddir = self.dataset_dir(name)
        if ddir.exists():
            shutil.rmtree(ddir, ignore_errors=True)
        wmeta = self.load_meta()
        wmeta["datasets"] = [d for d in wmeta.get("datasets", []) if d.get("name") != name]
        self.save_meta(wmeta)
        return True

    # ---- chat ----
    def append_chat_turn(self, turn: ChatTurn | Dict[str, Any]) -> None:
        if isinstance(turn, dict):
            turn = ChatTurn.from_dict(turn)
        self.chat_path.parent.mkdir(parents=True, exist_ok=True)
        with self.chat_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(turn.to_dict(), ensure_ascii=False, default=str) + "\n")
        self._trim_chat()

    def load_chat_history(self, limit: Optional[int] = None) -> List[ChatTurn]:
        if not self.chat_path.exists():
            return []
        turns: List[ChatTurn] = []
        try:
            with self.chat_path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        turns.append(ChatTurn.from_dict(json.loads(line)))
                    except Exception:
                        continue  # skip corrupt lines
        except Exception:
            return []
        if limit:
            turns = turns[-limit:]
        return turns

    def _trim_chat(self) -> None:
        """Keep only the last max_chat_turns (retention policy)."""
        turns = self.load_chat_history()
        if len(turns) <= self.max_chat_turns:
            return
        keep = turns[-self.max_chat_turns :]
        tmp = self.chat_path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for t in keep:
                f.write(json.dumps(t.to_dict(), ensure_ascii=False, default=str) + "\n")
        tmp.replace(self.chat_path)

    def clear_chat(self) -> None:
        if self.chat_path.exists():
            self.chat_path.unlink()

    # ---- high-level restore into a live Workspace ----
    def load_into(self, workspace: Any) -> Dict[str, Any]:
        """
        Restore all saved datasets into the given Workspace instance
        and re-register them in DuckDB.
        Returns a summary dict.
        """
        restored = []
        errors = []
        for name in self.list_saved_datasets():
            try:
                record = self.load_dataset_record(name)
                if record is None:
                    errors.append(f"{name}: missing or corrupt data")
                    continue
                # Avoid name collision: if already present, keep existing and warn
                if name in workspace.datasets:
                    errors.append(f"{name}: already in memory – skipped restore")
                    continue
                workspace.datasets[name] = record
                workspace.register_in_duckdb(name)
                restored.append(name)
            except Exception as e:
                errors.append(f"{name}: {e}")
        return {
            "workspace_id": self.workspace_id,
            "restored": restored,
            "errors": errors,
            "chat_turns": len(self.load_chat_history()),
        }

    # ---- export / import ----
    def export_snapshot(self, dest_zip: Path) -> Path:
        """Zip the entire workspace folder."""
        dest_zip = Path(dest_zip)
        dest_zip.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(dest_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in self.root.rglob("*"):
                if path.is_file():
                    zf.write(path, path.relative_to(self.root).as_posix())
        return dest_zip

    @classmethod
    def import_snapshot(
        cls,
        zip_path: Path,
        workspace_id: Optional[str] = None,
        root: Optional[Path] = None,
    ) -> "WorkspaceStore":
        """Import a previously exported zip into a new (or existing) workspace_id."""
        zip_path = Path(zip_path)
        if not zip_path.exists():
            raise FileNotFoundError(zip_path)
        wid = _safe_name(workspace_id or f"imported_{uuid.uuid4().hex[:8]}")
        store = cls(workspace_id=wid, root=root)
        # Clear target first
        if store.root.exists():
            shutil.rmtree(store.root, ignore_errors=True)
        store.root.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(store.root)
        # Ensure subdirs exist
        (store.root / "datasets").mkdir(exist_ok=True)
        (store.root / "chat").mkdir(exist_ok=True)
        (store.root / "catalog").mkdir(exist_ok=True)
        store.save_meta()  # touch updated_at
        return store

    # ---- utility ----
    def summary(self) -> Dict[str, Any]:
        meta = self.load_meta()
        return {
            "workspace_id": self.workspace_id,
            "path": str(self.root),
            "datasets": self.list_saved_datasets(),
            "dataset_count": len(self.list_saved_datasets()),
            "chat_turns": len(self.load_chat_history()),
            "created_at": meta.get("created_at"),
            "updated_at": meta.get("updated_at"),
        }


# ---------------------------------------------------------------------------
# Convenience helpers used by the Streamlit UI
# ---------------------------------------------------------------------------

def get_or_create_store(workspace_id: str = "default", **kwargs) -> WorkspaceStore:
    return WorkspaceStore(workspace_id=workspace_id, **kwargs)


def list_workspaces(root: Optional[Path] = None) -> List[str]:
    root = root or default_workspaces_root()
    if not root.exists():
        return []
    return sorted([p.name for p in root.iterdir() if p.is_dir()])


__all__ = [
    "ChatTurn",
    "WorkspaceStore",
    "get_or_create_store",
    "list_workspaces",
    "default_workspaces_root",
]
