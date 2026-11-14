"""Store and search vectors in PostgreSQL + pgvector (cosine, exact search, no ANN index)."""
import re
from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .models import Chunk

# Real columns that can be filtered directly; everything else is read from metadata JSONB.
_COLUMN_FILTERS = {"document_id", "source", "page"}
DEFAULT_FILTER_FIELDS = frozenset(
    {"document_id", "source", "page", "section", "year", "version", "category", "author", "paper",
     "audience", "language", "document_type", "effective_date"})
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in vector) + "]"


class PgVectorStore:
    def __init__(self, database_url: str, dimensions: int, table: str = "chunks",
                 filter_fields: frozenset[str] | set[str] = DEFAULT_FILTER_FIELDS) -> None:
        if not database_url:
            raise ValueError("database_url is empty")
        if dimensions <= 0:
            raise ValueError("dimensions must be positive")
        if not _IDENT.match(table):
            raise ValueError(f"invalid table name: {table!r}")
        self.database_url = database_url
        self.dimensions = dimensions
        self.table = table
        self.filter_fields = frozenset(filter_fields)

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(self.database_url, connect_timeout=5, row_factory=dict_row)

    def create_schema(self) -> None:
        """Create the table if needed; fail loudly if it exists with other dimensions."""
        table = sql.Identifier(self.table)
        with self._connect() as conn:
            conn.execute(sql.SQL("""
                CREATE TABLE IF NOT EXISTS {t} (
                    chunk_id    TEXT PRIMARY KEY,
                    strategy    TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    source      TEXT NOT NULL,
                    page        INT  NOT NULL,
                    chunk_index INT  NOT NULL,
                    text        TEXT NOT NULL,
                    metadata    JSONB NOT NULL DEFAULT '{{}}',
                    embedding   vector({d}) NOT NULL
                )""").format(t=table, d=sql.SQL(str(self.dimensions))))
            conn.execute(sql.SQL("CREATE INDEX IF NOT EXISTS {i} ON {t} (strategy, document_id)")
                         .format(i=sql.Identifier(f"idx_{self.table}_strategy"), t=table))
            row = conn.execute(
                "SELECT atttypmod AS dims FROM pg_attribute "
                "WHERE attrelid = %s::regclass AND attname = 'embedding'", (self.table,)).fetchone()
            if row and row["dims"] != self.dimensions:
                raise RuntimeError(f"table {self.table!r} has vector({row['dims']}) but the "
                                   f"embedder has {self.dimensions} dimensions")

    def upsert(self, strategy: str, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        """Idempotent write. Also removes chunks of the same documents that no longer exist."""
        if len(chunks) != len(vectors):
            raise ValueError(f"{len(chunks)} chunks but {len(vectors)} vectors")
        for chunk, vector in zip(chunks, vectors):
            if len(vector) != self.dimensions:
                raise ValueError(f"chunk {chunk.chunk_id}: vector has {len(vector)} dimensions, "
                                 f"expected {self.dimensions}")
        rows = [(c.chunk_id, strategy, c.document_id, c.source, c.page,
                 int(c.metadata.get("chunk_index", 0)), c.text, Jsonb(c.metadata),
                 _vector_literal(v)) for c, v in zip(chunks, vectors)]
        keep: dict[str, list[str]] = {}
        for c in chunks:
            keep.setdefault(c.document_id, []).append(c.chunk_id)
        table = sql.Identifier(self.table)
        with self._connect() as conn:  # one transaction: commit on success, rollback on error
            with conn.cursor() as cur:
                cur.executemany(sql.SQL("""
                    INSERT INTO {t} (chunk_id, strategy, document_id, source, page, chunk_index,
                                     text, metadata, embedding)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::vector)
                    ON CONFLICT (chunk_id) DO UPDATE SET
                        strategy = EXCLUDED.strategy, document_id = EXCLUDED.document_id,
                        source = EXCLUDED.source, page = EXCLUDED.page,
                        chunk_index = EXCLUDED.chunk_index, text = EXCLUDED.text,
                        metadata = EXCLUDED.metadata, embedding = EXCLUDED.embedding
                    """).format(t=table), rows)
                for document_id, ids in keep.items():  # obsolete chunks of re-ingested documents
                    cur.execute(sql.SQL("DELETE FROM {t} WHERE strategy = %s AND document_id = %s "
                                        "AND chunk_id <> ALL(%s)").format(t=table),
                                (strategy, document_id, ids))

    def count(self, strategy: str | None = None) -> int:
        query = sql.SQL("SELECT count(*) AS n FROM {t}").format(t=sql.Identifier(self.table))
        params: tuple = ()
        if strategy is not None:
            query += sql.SQL(" WHERE strategy = %s")
            params = (strategy,)
        with self._connect() as conn:
            return int(conn.execute(query, params).fetchone()["n"])

    def drop_table(self) -> None:
        """Remove the whole table (used by tests on a scratch table)."""
        with self._connect() as conn:
            conn.execute(sql.SQL("DROP TABLE IF EXISTS {t}").format(t=sql.Identifier(self.table)))

    def search(self, strategy: str, query_vector: list[float], top_k: int = 3,
               filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Top-k by cosine similarity. Filters are applied in SQL BEFORE ranking.

        Only fields in `filter_fields` are allowed; values are always bound parameters.
        A filter value may be a scalar or a list (match any).
        """
        if len(query_vector) != self.dimensions:
            raise ValueError(f"query vector has {len(query_vector)} dimensions, "
                             f"expected {self.dimensions}")
        vector = _vector_literal(query_vector)
        conditions = [sql.SQL("strategy = %s")]
        params: list[Any] = [vector, strategy]
        for field, value in (filters or {}).items():
            if field not in self.filter_fields:
                raise ValueError(f"filter field not allowed: {field!r}")
            values = list(value) if isinstance(value, (list, tuple, set)) else [value]
            if field in _COLUMN_FILTERS:
                conditions.append(sql.SQL("{c} = ANY(%s)").format(c=sql.Identifier(field)))
                params.append([int(v) for v in values] if field == "page" else [str(v) for v in values])
            else:
                conditions.append(sql.SQL("metadata->>%s = ANY(%s)"))
                params.extend([field, [str(v) for v in values]])
        params.extend([vector, top_k])
        query = sql.SQL("""
            SELECT chunk_id, document_id, source, page, chunk_index, text, metadata,
                   1 - (embedding <=> %s::vector) AS score
            FROM {t} WHERE {where}
            ORDER BY embedding <=> %s::vector
            LIMIT %s""").format(t=sql.Identifier(self.table),
                                where=sql.SQL(" AND ").join(conditions))
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        for rank, row in enumerate(rows, start=1):
            row["rank"] = rank
            row["score"] = float(row["score"])
        return rows
