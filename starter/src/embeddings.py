"""Real embedding model (local Sentence Transformers); never falls back to mock vectors."""
import os
from pathlib import Path
from typing import Protocol


class Embedder(Protocol):
    model_name: str
    dimensions: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Return one vector per input text, in the same order."""
        ...

    def embed_query(self, question: str) -> list[float]:
        """Return a query vector compatible with the stored document vectors."""
        ...


class MyEmbedder:
    """Sentence Transformers adapter. E5 models need 'passage: ' / 'query: ' prefixes.

    Documents and queries use the same model and both are L2-normalized, so cosine
    distance in pgvector is meaningful.
    """

    def __init__(self, model_name: str) -> None:
        if not model_name:
            raise ValueError("EMBEDDING_MODEL is empty: set it in starter/.env")
        self.model_name = model_name
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError("sentence-transformers is not installed: "
                               "python -m pip install sentence-transformers") from exc
        self._model = SentenceTransformer(model_name)  # raises if the model cannot be loaded
        get_dims = getattr(self._model, "get_embedding_dimension", None) \
            or self._model.get_sentence_embedding_dimension  # renamed in newer versions
        self.dimensions = int(get_dims())
        self._e5 = "e5" in model_name.lower()

    @classmethod
    def from_env(cls) -> "MyEmbedder":
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[1] / ".env")
        return cls(os.getenv("EMBEDDING_MODEL", ""))

    def embed_documents(self, texts: list[str], batch_size: int = 32) -> list[list[float]]:
        if not texts:
            return []
        inputs = [f"passage: {t}" for t in texts] if self._e5 else list(texts)
        vectors = self._model.encode(inputs, batch_size=batch_size, normalize_embeddings=True,
                                     show_progress_bar=False)
        return vectors.tolist()

    def embed_query(self, question: str) -> list[float]:
        text = f"query: {question}" if self._e5 else question
        vector = self._model.encode([text], normalize_embeddings=True, show_progress_bar=False)
        return vector[0].tolist()
