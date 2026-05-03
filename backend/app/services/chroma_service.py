"""Chroma vector store service for RAG schema retrieval.

Each datasource gets its own Chroma collection (rag_{datasource_id}).
Table and column descriptions are stored as documents with metadata,
vectorized via Chroma's default embedding function (sentence-transformers).
"""
import hashlib
import json
import os
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

try:
    import chromadb
    from chromadb.utils import embedding_functions

    _CHROMA_AVAILABLE = True
except ImportError:
    _CHROMA_AVAILABLE = False


def _build_document(table: dict) -> str:
    """Concatenate table/column names and comments into a single document string."""
    parts = []
    t_name = table.get("name", "")
    t_desc = table.get("description", "") or ""
    t_alias = table.get("alias", "") or ""
    parts.append(f"table: {t_name}")
    if t_desc:
        parts.append(f"description: {t_desc}")
    if t_alias:
        parts.append(f"alias: {t_alias}")

    for col in table.get("columns", []):
        c_name = col.get("name", "")
        c_comment = col.get("comment", "") or ""
        c_alias = col.get("alias", "") or ""
        c_type = col.get("type", "") or ""
        parts.append(f"column: {c_name} ({c_type})")
        if c_comment:
            parts.append(f"  comment: {c_comment}")
        if c_alias:
            parts.append(f"  alias: {c_alias}")

    return "\n".join(parts)


def _compute_doc_id(table: dict, column_name: str | None = None) -> str:
    """Generate a stable document ID for a table or column."""
    if column_name:
        raw = f"{table['name']}.{column_name}"
    else:
        raw = table["name"]
    return hashlib.md5(raw.encode()).hexdigest()


class ChromaService:
    """Wraps a Chroma PersistentClient for per-datasource RAG collections."""

    def __init__(self, path: str | None = None):
        self._path = path or settings.chroma_path
        self._client: chromadb.PersistentClient | None = None
        self._embedding = None

    @property
    def client(self) -> chromadb.PersistentClient:
        if self._client is None:
            os.makedirs(self._path, exist_ok=True)
            self._client = chromadb.PersistentClient(path=self._path)
        return self._client

    @property
    def embedding(self):
        if self._embedding is None:
            self._embedding = embedding_functions.DefaultEmbeddingFunction()
        return self._embedding

    def _collection_name(self, datasource_id: str) -> str:
        return f"rag_{datasource_id}"

    def ensure_collection(self, datasource_id: str, tables: list[dict]) -> None:
        """Create or update a Chroma collection for the given datasource.

        Each table produces one document (table-level). Each column produces
        one additional document (column-level). Both are stored in the same
        collection with metadata for filtering.

        Tables: list[dict] from the metadata JSON "models" array.
        """
        col_name = self._collection_name(datasource_id)

        # Delete existing collection to ensure clean upsert
        try:
            self.client.delete_collection(name=col_name)
        except Exception:
            pass

        collection = self.client.create_collection(
            name=col_name,
            embedding_function=self.embedding,
            metadata={"datasource_id": datasource_id},
        )

        documents = []
        metadatas = []
        ids = []

        for table in tables:
            t_name = table.get("name", "")
            if not t_name:
                continue

            # Table-level document
            doc_id = _compute_doc_id(table)
            documents.append(_build_document(table))
            metadatas.append({
                "datasource_id": datasource_id,
                "table_name": t_name,
                "column_name": "",
                "table_comment": table.get("description", "") or "",
                "doc_type": "table",
            })
            ids.append(doc_id)

            # Column-level documents
            for col in table.get("columns", []):
                c_name = col.get("name", "")
                if not c_name:
                    continue
                col_doc_id = _compute_doc_id(table, c_name)
                col_doc = _build_document({
                    "name": t_name,
                    "description": table.get("description", "") or "",
                    "alias": table.get("alias", "") or "",
                    "columns": [col],
                })
                documents.append(col_doc)
                metadatas.append({
                    "datasource_id": datasource_id,
                    "table_name": t_name,
                    "column_name": c_name,
                    "table_comment": table.get("description", "") or "",
                    "column_comment": col.get("comment", "") or "",
                    "column_type": col.get("type", "") or "",
                    "doc_type": "column",
                })
                ids.append(col_doc_id)

        if documents:
            collection.add(
                documents=documents,
                metadatas=metadatas,
                ids=ids,
            )

        logger.info(
            "Chroma collection '%s' created with %d documents (%d tables)",
            col_name, len(documents), len(tables),
        )

    def query_similar(
        self,
        datasource_id: str,
        query_text: str,
        max_results: int = 5,
        n_results: int = 20,
    ) -> dict[str, Any]:
        """Query Chroma for tables/columns similar to the user's question.

        Returns: {
            "tables": [
                {
                    "name": str,
                    "description": str,
                    "score": float,
                    "matched_columns": [
                        {"name": str, "comment": str, "type": str, "score": float}
                    ],
                }
            ]
        }
        """
        col_name = self._collection_name(datasource_id)
        collection = self.client.get_collection(name=col_name)

        results = collection.query(
            query_texts=[query_text],
            n_results=n_results,
            include=["metadatas", "distances"],
        )

        # results["metadatas"] and results["distances"] are lists of lists
        # (outer list = per query, inner list = per result)
        metadatas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]

        # Group by table_name, track best score per table
        table_scores: dict[str, float] = {}
        table_columns: dict[str, list[dict]] = {}
        table_comments: dict[str, str] = {}

        for meta, dist in zip(metadatas, distances):
            t_name = meta.get("table_name", "")
            score = 1.0 - dist  # convert distance to similarity-like score
            doc_type = meta.get("doc_type", "")

            if doc_type == "table":
                if t_name not in table_scores or score > table_scores[t_name]:
                    table_scores[t_name] = score
                table_comments[t_name] = meta.get("table_comment", "") or ""
            elif doc_type == "column":
                c_name = meta.get("column_name", "")
                if t_name not in table_scores or score > table_scores[t_name]:
                    table_scores[t_name] = score * 0.7  # column match weights less
                if t_name not in table_columns:
                    table_columns[t_name] = []
                table_columns[t_name].append({
                    "name": c_name,
                    "comment": meta.get("column_comment", "") or "",
                    "type": meta.get("column_type", "") or "",
                    "score": round(score, 4),
                })
                table_comments.setdefault(t_name, meta.get("table_comment", "") or "")

        # Sort tables by score, take top max_results
        sorted_tables = sorted(table_scores.items(), key=lambda x: -x[1])
        result_tables = []
        for t_name, score in sorted_tables[:max_results]:
            result_tables.append({
                "name": t_name,
                "description": table_comments.get(t_name, ""),
                "score": round(score, 4),
                "matched_columns": table_columns.get(t_name, []),
            })

        return {"tables": result_tables}

    def delete_collection(self, datasource_id: str) -> None:
        """Remove Chroma collection for a deleted datasource."""
        col_name = self._collection_name(datasource_id)
        try:
            self.client.delete_collection(name=col_name)
            logger.info("Chroma collection '%s' deleted", col_name)
        except Exception:
            # Collection might not exist — ignore
            pass

    def refresh_collection(self, datasource_id: str, tables: list[dict]) -> None:
        """Delete and recreate collection for schema sync."""
        self.delete_collection(datasource_id)
        if tables:
            self.ensure_collection(datasource_id, tables)


# Singleton instance
_chroma_service: ChromaService | None = None


def get_chroma_service() -> ChromaService | None:
    """Return the ChromaService singleton, or None if chromadb is unavailable."""
    global _chroma_service
    if not _CHROMA_AVAILABLE:
        return None
    if _chroma_service is None:
        _chroma_service = ChromaService()
    return _chroma_service
