"""
InsightForgeAI – Knowledge Base & RAG (Phase 4.6)

Local document store for SOPs, policies, past reports (PDF / Markdown / TXT).
- Chunk → persist under data/workspaces/{id}/knowledge/
- Retrieval: pure-Python TF-IDF cosine (no heavy embedding model required)
- Hybrid answers: LLM must cite chunk ids; refuse when no relevant evidence

Design goals
------------
- Zero required new heavy deps (pypdf optional for PDF)
- Fail closed: corrupt files skipped, empty retrieval → clear message
- Workspace-scoped so multi-tenant workspaces stay isolated
- Citations are first-class (source, page/section, chunk_id)
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def _project_root() -> Path:
    here = Path(__file__).resolve()
    for p in [here.parents[2], Path.cwd()]:
        if (p / "app").exists() or (p / "data").exists():
            return p
    return Path.cwd()


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _safe_name(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_\-\.]+", "_", (name or "doc").strip()) or "doc"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

@dataclass
class KnowledgeChunk:
    id: str
    doc_id: str
    source: str
    text: str
    chunk_index: int
    start_char: int = 0
    end_char: int = 0
    meta: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "KnowledgeChunk":
        return cls(
            id=str(d.get("id") or uuid.uuid4().hex[:12]),
            doc_id=str(d.get("doc_id") or ""),
            source=str(d.get("source") or ""),
            text=str(d.get("text") or ""),
            chunk_index=int(d.get("chunk_index") or 0),
            start_char=int(d.get("start_char") or 0),
            end_char=int(d.get("end_char") or 0),
            meta=dict(d.get("meta") or {}),
            created_at=str(d.get("created_at") or _now_iso()),
        )


@dataclass
class RetrievalHit:
    chunk: KnowledgeChunk
    score: float
    rank: int

    def to_citation(self) -> Dict[str, Any]:
        return {
            "type": "document",
            "chunk_id": self.chunk.id,
            "doc_id": self.chunk.doc_id,
            "source": self.chunk.source,
            "score": round(self.score, 4),
            "excerpt": (self.chunk.text or "")[:280],
            "chunk_index": self.chunk.chunk_index,
            "meta": dict(self.chunk.meta or {}),
        }


@dataclass
class IngestResult:
    success: bool
    doc_id: str = ""
    source: str = ""
    n_chunks: int = 0
    error: Optional[str] = None
    warnings: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Text extraction & chunking
# ---------------------------------------------------------------------------

def extract_text_from_bytes(data: bytes, filename: str) -> Tuple[str, List[str]]:
    """Return (text, warnings). Supports .txt, .md, .pdf (pypdf optional)."""
    warnings: List[str] = []
    name = (filename or "").lower()
    if name.endswith((".txt", ".md", ".markdown", ".csv")):
        for enc in ("utf-8", "utf-8-sig", "latin-1"):
            try:
                return data.decode(enc), warnings
            except Exception:
                continue
        return data.decode("utf-8", errors="replace"), ["Used lossy decode"]

    if name.endswith(".pdf"):
        try:
            from pypdf import PdfReader  # type: ignore
            import io

            reader = PdfReader(io.BytesIO(data))
            pages = []
            for i, page in enumerate(reader.pages):
                try:
                    t = page.extract_text() or ""
                except Exception:
                    t = ""
                    warnings.append(f"page {i+1}: extract failed")
                if t.strip():
                    pages.append(f"[Page {i+1}]\n{t}")
            text = "\n\n".join(pages)
            if not text.strip():
                return "", ["PDF produced no extractable text (scanned image?)"]
            return text, warnings
        except ImportError:
            return "", ["pypdf not installed – run: pip install pypdf"]
        except Exception as e:
            return "", [f"PDF parse error: {e}"]

    # Fallback: treat as text
    try:
        return data.decode("utf-8", errors="replace"), ["Unknown extension – treated as text"]
    except Exception as e:
        return "", [str(e)]


def _normalize_whitespace(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def chunk_text(
    text: str,
    *,
    chunk_size: int = 900,
    overlap: int = 120,
) -> List[Tuple[str, int, int]]:
    """
    Character-based sliding window with paragraph preference.
    Returns list of (chunk_text, start_char, end_char).
    """
    text = _normalize_whitespace(text or "")
    if not text:
        return []
    if len(text) <= chunk_size:
        return [(text, 0, len(text))]

    chunks: List[Tuple[str, int, int]] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + chunk_size, n)
        # Prefer break at paragraph / sentence near the end
        if end < n:
            window = text[start:end]
            for sep in ("\n\n", "\n", ". ", "; "):
                pos = window.rfind(sep)
                if pos >= chunk_size // 3:
                    end = start + pos + len(sep)
                    break
        piece = text[start:end].strip()
        if piece:
            chunks.append((piece, start, end))
        if end >= n:
            break
        start = max(end - overlap, start + 1)
    return chunks


# ---------------------------------------------------------------------------
# Pure-Python TF-IDF retrieval (no sklearn required)
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_\-]{1,40}", re.I)


def _tokenize(text: str) -> List[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text or "")]


def _tf(tokens: List[str]) -> Dict[str, float]:
    if not tokens:
        return {}
    counts: Dict[str, int] = {}
    for t in tokens:
        counts[t] = counts.get(t, 0) + 1
    n = float(len(tokens))
    return {k: v / n for k, v in counts.items()}


def _cosine(a: Dict[str, float], b: Dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    keys = set(a) & set(b)
    if not keys:
        return 0.0
    dot = sum(a[k] * b[k] for k in keys)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    if na < 1e-12 or nb < 1e-12:
        return 0.0
    return float(dot / (na * nb))


class KnowledgeStore:
    """
    Workspace-scoped document knowledge base.

    Layout:
      data/workspaces/{workspace_id}/knowledge/
        manifest.json
        chunks.jsonl
        docs/{doc_id}.meta.json
    """

    def __init__(self, workspace_id: str = "default", root: Optional[Path] = None):
        self.workspace_id = _safe_name(workspace_id)
        base = root or (_project_root() / "data" / "workspaces")
        self.root = Path(base) / self.workspace_id / "knowledge"
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "docs").mkdir(exist_ok=True)
        self._chunks: Optional[List[KnowledgeChunk]] = None
        self._idf: Optional[Dict[str, float]] = None

    # ---- paths ----
    @property
    def chunks_path(self) -> Path:
        return self.root / "chunks.jsonl"

    @property
    def manifest_path(self) -> Path:
        return self.root / "manifest.json"

    def doc_meta_path(self, doc_id: str) -> Path:
        return self.root / "docs" / f"{_safe_name(doc_id)}.meta.json"

    # ---- persistence ----
    def _load_chunks(self) -> List[KnowledgeChunk]:
        if self._chunks is not None:
            return self._chunks
        out: List[KnowledgeChunk] = []
        if not self.chunks_path.exists():
            self._chunks = out
            return out
        try:
            with self.chunks_path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(KnowledgeChunk.from_dict(json.loads(line)))
                    except Exception:
                        continue
        except Exception:
            out = []
        self._chunks = out
        self._idf = None
        return out

    def _save_chunks(self, chunks: List[KnowledgeChunk]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.chunks_path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for c in chunks:
                f.write(json.dumps(c.to_dict(), ensure_ascii=False, default=str) + "\n")
        tmp.replace(self.chunks_path)
        self._chunks = chunks
        self._idf = None

    def _write_manifest(self, docs: List[Dict[str, Any]]) -> None:
        data = {
            "workspace_id": self.workspace_id,
            "updated_at": _now_iso(),
            "doc_count": len(docs),
            "chunk_count": len(self._load_chunks()),
            "docs": docs,
        }
        tmp = self.manifest_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        tmp.replace(self.manifest_path)

    def list_documents(self) -> List[Dict[str, Any]]:
        if self.manifest_path.exists():
            try:
                data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
                return list(data.get("docs") or [])
            except Exception:
                pass
        # rebuild from chunks
        by_doc: Dict[str, Dict[str, Any]] = {}
        for c in self._load_chunks():
            if c.doc_id not in by_doc:
                by_doc[c.doc_id] = {
                    "doc_id": c.doc_id,
                    "source": c.source,
                    "n_chunks": 0,
                    "created_at": c.created_at,
                }
            by_doc[c.doc_id]["n_chunks"] += 1
        return list(by_doc.values())

    # ---- ingest ----
    def ingest_text(
        self,
        text: str,
        source: str,
        *,
        doc_id: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
        chunk_size: int = 900,
        overlap: int = 120,
    ) -> IngestResult:
        text = _normalize_whitespace(text or "")
        if not text:
            return IngestResult(success=False, source=source, error="Empty document text")

        doc_id = doc_id or hashlib.sha1(f"{source}:{len(text)}:{text[:200]}".encode()).hexdigest()[:12]
        pieces = chunk_text(text, chunk_size=chunk_size, overlap=overlap)
        if not pieces:
            return IngestResult(success=False, source=source, error="Chunking produced no pieces")

        # Remove previous chunks for same source (re-ingest)
        existing = [c for c in self._load_chunks() if c.source != source and c.doc_id != doc_id]
        new_chunks: List[KnowledgeChunk] = []
        for i, (piece, start, end) in enumerate(pieces):
            new_chunks.append(
                KnowledgeChunk(
                    id=f"{doc_id}_{i:04d}",
                    doc_id=doc_id,
                    source=source,
                    text=piece,
                    chunk_index=i,
                    start_char=start,
                    end_char=end,
                    meta=dict(meta or {}),
                )
            )
        all_chunks = existing + new_chunks
        self._save_chunks(all_chunks)

        docs = [d for d in self.list_documents() if d.get("doc_id") != doc_id and d.get("source") != source]
        docs.append(
            {
                "doc_id": doc_id,
                "source": source,
                "n_chunks": len(new_chunks),
                "created_at": _now_iso(),
                "meta": dict(meta or {}),
            }
        )
        self._write_manifest(docs)
        # also write per-doc meta
        self.doc_meta_path(doc_id).write_text(
            json.dumps(
                {"doc_id": doc_id, "source": source, "n_chunks": len(new_chunks), "created_at": _now_iso(), "meta": meta or {}},
                indent=2,
            ),
            encoding="utf-8",
        )
        return IngestResult(success=True, doc_id=doc_id, source=source, n_chunks=len(new_chunks))

    def ingest_file_bytes(self, data: bytes, filename: str, meta: Optional[Dict[str, Any]] = None) -> IngestResult:
        text, warns = extract_text_from_bytes(data, filename)
        if not text.strip():
            return IngestResult(success=False, source=filename, error="No text extracted", warnings=warns)
        result = self.ingest_text(text, source=_safe_name(filename), meta=meta)
        result.warnings.extend(warns)
        return result

    def delete_document(self, doc_id: Optional[str] = None, source: Optional[str] = None) -> int:
        chunks = self._load_chunks()
        before = len(chunks)
        if doc_id:
            chunks = [c for c in chunks if c.doc_id != doc_id]
        if source:
            chunks = [c for c in chunks if c.source != source]
        removed = before - len(chunks)
        if removed:
            self._save_chunks(chunks)
            docs = self.list_documents()
            if doc_id:
                docs = [d for d in docs if d.get("doc_id") != doc_id]
            if source:
                docs = [d for d in docs if d.get("source") != source]
            self._write_manifest(docs)
        return removed

    def clear(self) -> None:
        self._save_chunks([])
        self._write_manifest([])
        for p in (self.root / "docs").glob("*.json"):
            try:
                p.unlink()
            except Exception:
                pass

    # ---- retrieval ----
    def _build_idf(self, chunks: List[KnowledgeChunk]) -> Dict[str, float]:
        if self._idf is not None:
            return self._idf
        N = max(1, len(chunks))
        df: Dict[str, int] = {}
        for c in chunks:
            for t in set(_tokenize(c.text)):
                df[t] = df.get(t, 0) + 1
        idf = {t: math.log((N + 1) / (cnt + 1)) + 1.0 for t, cnt in df.items()}
        self._idf = idf
        return idf

    def retrieve(
        self,
        query: str,
        top_k: int = 5,
        min_score: float = 0.04,
    ) -> List[RetrievalHit]:
        chunks = self._load_chunks()
        if not chunks or not (query or "").strip():
            return []
        idf = self._build_idf(chunks)
        q_tokens = _tokenize(query)
        q_tf = _tf(q_tokens)
        q_vec = {t: q_tf.get(t, 0.0) * idf.get(t, 1.0) for t in q_tf}

        scored: List[Tuple[float, KnowledgeChunk]] = []
        for c in chunks:
            c_tf = _tf(_tokenize(c.text))
            c_vec = {t: c_tf.get(t, 0.0) * idf.get(t, 1.0) for t in c_tf}
            score = _cosine(q_vec, c_vec)
            # small boost for exact phrase / title-ish overlap
            if query.lower()[:40] in (c.text or "").lower():
                score += 0.08
            if score >= min_score:
                scored.append((score, c))

        scored.sort(key=lambda x: x[0], reverse=True)
        hits: List[RetrievalHit] = []
        for rank, (score, chunk) in enumerate(scored[: max(1, top_k)], start=1):
            hits.append(RetrievalHit(chunk=chunk, score=score, rank=rank))
        return hits

    def answer(
        self,
        question: str,
        *,
        top_k: int = 5,
        llm_client: Any = None,
    ) -> Dict[str, Any]:
        """
        Retrieve + generate grounded answer.
        Always returns citations; refuses when no relevant chunks.
        """
        hits = self.retrieve(question, top_k=top_k)
        citations = [h.to_citation() for h in hits]

        if not hits:
            return {
                "success": False,
                "answer": (
                    "I could not find this in the uploaded knowledge base (policies / SOPs / reports). "
                    "Upload a relevant document, or ask a data question about the loaded tables."
                ),
                "citations": [],
                "grounding_line": "Used: knowledge base (no matching chunks)",
                "hits": 0,
            }

        context_blocks = []
        for h in hits:
            context_blocks.append(
                f"[chunk_id={h.chunk.id} | source={h.chunk.source} | score={h.score:.3f}]\n{h.chunk.text}"
            )
        context = "\n\n---\n\n".join(context_blocks)

        system = (
            "You are InsightForgeAI's knowledge assistant for company policies and SOPs.\n"
            "Answer ONLY using the provided document chunks.\n"
            "Rules:\n"
            "- If the chunks do not contain the answer, say you cannot find it in the knowledge base.\n"
            "- Cite every factual claim with the chunk_id in square brackets, e.g. [chunk_id=abc_0001].\n"
            "- Do not invent policies, numbers, or procedures.\n"
            "- Keep the answer concise (under 180 words).\n"
        )
        user = f"QUESTION: {question}\n\nDOCUMENT CHUNKS:\n{context}"

        answer_text = ""
        provider = model = None
        if llm_client is not None and getattr(llm_client, "is_configured", lambda: False)():
            try:
                resp = llm_client.chat(system_prompt=system, user_prompt=user, temperature=0.1, max_tokens=400)
                if resp.success and (resp.content or "").strip():
                    answer_text = resp.content.strip()
                    provider = resp.provider
                    model = resp.model
            except Exception:
                answer_text = ""

        if not answer_text:
            # Deterministic extractive fallback
            best = hits[0].chunk
            answer_text = (
                f"From **{best.source}** (chunk {best.id}):\n\n"
                f"{best.text[:600]}{'…' if len(best.text) > 600 else ''}\n\n"
                f"[chunk_id={best.id}]"
            )

        sources = sorted({h.chunk.source for h in hits})
        grounding = f"Used: knowledge · {', '.join(sources[:3])}" + (f" (+{len(sources)-3} more)" if len(sources) > 3 else "")

        return {
            "success": True,
            "answer": answer_text,
            "citations": citations,
            "grounding_line": grounding,
            "hits": len(hits),
            "provider": provider,
            "model": model,
        }

    def summary(self) -> Dict[str, Any]:
        docs = self.list_documents()
        return {
            "workspace_id": self.workspace_id,
            "path": str(self.root),
            "doc_count": len(docs),
            "chunk_count": len(self._load_chunks()),
            "docs": docs,
        }


def get_knowledge_store(workspace_id: str = "default", root: Optional[Path] = None) -> KnowledgeStore:
    return KnowledgeStore(workspace_id=workspace_id, root=root)


__all__ = [
    "KnowledgeChunk",
    "RetrievalHit",
    "IngestResult",
    "KnowledgeStore",
    "get_knowledge_store",
    "chunk_text",
    "extract_text_from_bytes",
]
