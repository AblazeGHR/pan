"""Embedding layer — OpenAI, Ollama, sentence-transformers, and llama.cpp providers.

Supports four providers:
- ``"openai"``       — text-embedding-3-small (1536 dims), requires API key
- ``"ollama"``       — Ollama local API (nomic-embed-text, 768 dims)
- ``"sentence-transformers"`` — HuggingFace models via sentence-transformers (384 dims by default)
- ``"local"``        — llama.cpp GGUF model (requires llama-cpp-python)

All embeddings are cached in MemoryStore to avoid redundant computation.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .store import MemoryStore

log = logging.getLogger(__name__)

# ── Providers ──────────────────────────────────────────────────────── #

PROVIDER_OPENAI = "openai"
PROVIDER_OLLAMA = "ollama"
PROVIDER_SENTENCE_TRANSFORMERS = "sentence-transformers"
PROVIDER_LOCAL = "local"

# Model-specific dimensions
OPENAI_DEFAULT_MODEL = "text-embedding-3-small"
OPENAI_DIMS = 1536
OPENAI_MAX_TOKENS = 8191

# Ollama defaults
OLLAMA_DEFAULT_MODEL = "nomic-embed-text"
OLLAMA_BASE_URL = "http://127.0.0.1:11434"
OLLAMA_DIMS = 768

# Sentence-transformers defaults
ST_DEFAULT_MODEL = os.environ.get("PAN_ST_MODEL", "BAAI/bge-base-zh-v1.5")
ST_DIMS = int(os.environ.get("PAN_ST_DIMS", "768"))

# llama.cpp GGUF defaults
LOCAL_DEFAULT_MODEL = "embeddinggemma-300m-qat-Q8_0.gguf"
LOCAL_DIMS = 768
LOCAL_CONTEXT_SIZE = 8192

# Default GGUF download URI
LOCAL_DEFAULT_MODEL_URI = (
    "hf:ggml-org/embeddinggemma-300m-qat-q8_0-GGUF/"
    "embeddinggemma-300m-qat-Q8_0.gguf"
)


@dataclass
class EmbeddingError(Exception):
    """Raised when embedding computation fails."""

    message: str


class Embedder:
    """Compute embeddings via OpenAI API or local llama.cpp model."""

    def __init__(
        self,
        store: MemoryStore,
        provider: str = PROVIDER_OPENAI,
        model: str | None = None,
        model_path: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self._store = store
        self._provider = provider
        self._api_key = api_key
        self._base_url = base_url or (
            OLLAMA_BASE_URL if provider == PROVIDER_OLLAMA else None
        )

        # Resolve model and dimensions
        if model is not None:
            self._model = model
        elif provider == PROVIDER_OLLAMA:
            self._model = OLLAMA_DEFAULT_MODEL
        elif provider == PROVIDER_SENTENCE_TRANSFORMERS:
            self._model = ST_DEFAULT_MODEL
        elif provider == PROVIDER_LOCAL:
            self._model = LOCAL_DEFAULT_MODEL
        else:
            self._model = OPENAI_DEFAULT_MODEL

        if provider == PROVIDER_OLLAMA:
            self._dims = OLLAMA_DIMS
        elif provider == PROVIDER_SENTENCE_TRANSFORMERS:
            self._dims = ST_DIMS
        elif provider == PROVIDER_LOCAL:
            self._dims = LOCAL_DIMS
        else:
            self._dims = OPENAI_DIMS

        # Local model setup (lazy-loaded)
        self._model_path = model_path
        self._local_model: object | None = None
        self._st_model: object | None = None  # sentence-transformers

        self._provider_key = self._cache_key()

    @property
    def dims(self) -> int:
        """Embedding vector dimension for the active provider/model."""
        return self._dims

    @property
    def model_name(self) -> str:
        """Name of the actual model in use (not a constant)."""
        return self._model

    @property
    def provider(self) -> str:
        """Active provider name (openai / ollama / sentence-transformers / local)."""
        return self._provider

    # ------------------------------------------------------------------ #
    #  Public API
    # ------------------------------------------------------------------ #

    def embed(self, text: str) -> list[float]:
        """Return the embedding vector for a single text."""
        if not text:
            return [0.0] * self._dims

        text = self._truncate_if_needed(text)

        cached = self._check_cache(text)
        if cached is not None:
            return cached

        results = self._embed_uncached([text])
        return results[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts, reusing cached embeddings."""
        if not texts:
            return []

        results: list[list[float] | None] = [None] * len(texts)
        to_embed: list[str] = []
        to_embed_indices: list[int] = []

        for i, raw in enumerate(texts):
            if not raw:
                results[i] = [0.0] * self._dims
                continue

            text = self._truncate_if_needed(raw)
            cached = self._check_cache(text)
            if cached is not None:
                results[i] = cached
            else:
                to_embed.append(text)
                to_embed_indices.append(i)

        if to_embed:
            embedded = self._embed_uncached(to_embed)
            for j, idx in enumerate(to_embed_indices):
                results[idx] = embedded[j]

        assert all(r is not None for r in results)
        return results  # type: ignore[return-value]

    # ------------------------------------------------------------------ #
    #  Provider: OpenAI
    # ------------------------------------------------------------------ #

    def _embed_openai(self, texts: list[str]) -> list[list[float]]:
        """Call OpenAI embeddings API."""
        if not self._api_key:
            raise EmbeddingError(
                "API key is not set. Provide api_key to Embedder or set "
                "the OPENAI_API_KEY environment variable."
            )

        try:
            import openai
        except ImportError:
            raise EmbeddingError(
                "The 'openai' package is required for OpenAI embeddings. "
                "Install it with: pip install openai"
            ) from None

        client = openai.OpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
        )

        last_exc: Exception | None = None
        for attempt in (1, 2):
            try:
                resp = client.embeddings.create(
                    input=texts,
                    model=self._model,
                    dimensions=OPENAI_DIMS,
                )
                break
            except Exception as exc:
                last_exc = exc
                if attempt == 1:
                    log.warning(
                        "Embedding API call failed (attempt 1/2), retrying: %s", exc
                    )
                    time.sleep(1)
                else:
                    raise EmbeddingError(
                        f"OpenAI embeddings API call failed after 2 attempts: {exc}"
                    ) from exc

        return [d.embedding for d in resp.data]  # type: ignore[union-attr]

    # ------------------------------------------------------------------ #
    #  Provider: Ollama
    # ------------------------------------------------------------------ #

    def _embed_ollama(self, texts: list[str]) -> list[list[float]]:
        """Compute embeddings via Ollama's local API.

        Uses POST {base_url}/api/embed with {"model": ..., "input": [...]}.
        No API key required — Ollama runs locally.
        """
        import urllib.request

        base_url = (self._base_url or OLLAMA_BASE_URL).rstrip("/")
        url = f"{base_url}/api/embed"

        payload = json.dumps({
            "model": self._model,
            "input": texts,
        }).encode("utf-8")

        last_exc: Exception | None = None
        for attempt in (1, 2):
            try:
                req = urllib.request.Request(
                    url,
                    data=payload,
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=120) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                break
            except Exception as exc:
                last_exc = exc
                if attempt == 1:
                    log.warning(
                        "Ollama embedding call failed (attempt 1/2), retrying: %s", exc
                    )
                    time.sleep(1)
                else:
                    raise EmbeddingError(
                        f"Ollama embeddings API call failed after 2 attempts: {exc}. "
                        "Is Ollama running? (ollama serve)"
                    ) from exc

        embeddings = data.get("embeddings", [])
        if not embeddings or len(embeddings) != len(texts):
            raise EmbeddingError(
                f"Ollama returned {len(embeddings)} embeddings for {len(texts)} inputs"
            )

        return [list(emb) for emb in embeddings]

    # ------------------------------------------------------------------ #
    #  Provider: Local (llama.cpp GGUF)
    # ------------------------------------------------------------------ #

    def _ensure_local_model(self):
        """Lazy-load the GGUF model via llama-cpp-python."""
        if self._local_model is not None:
            return

        try:
            from llama_cpp import Llama
        except ImportError:
            raise EmbeddingError(
                "The 'llama-cpp-python' package is required for local embeddings. "
                "Install it with: pip install llama-cpp-python"
            ) from None

        model_path = self._model_path
        if not model_path:
            # Try common locations
            from pathlib import Path as _Path
            candidates = [
                _Path.home() / ".cache" / "node-llama-cpp" / self._model,
                _Path.home() / ".cache" / "llama-cpp" / self._model,
            ]
            for c in candidates:
                if c.exists():
                    model_path = str(c)
                    break

        if not model_path or not _Path(model_path).exists():
            raise EmbeddingError(
                f"Local embedding model not found: {self._model}. "
                "Set model_path to the GGUF file location, or download via: "
                f"huggingface-cli download {LOCAL_DEFAULT_MODEL_URI}"
            )

        log.info("Loading local embedding model: %s", model_path)
        self._local_model = Llama(
            model_path=model_path,
            embedding=True,
            n_ctx=LOCAL_CONTEXT_SIZE,
            verbose=False,
        )
        log.info("Local embedding model loaded (%d dims)", self._dims)

    def _embed_local(self, texts: list[str]) -> list[list[float]]:
        """Compute embeddings using local GGUF model."""
        self._ensure_local_model()
        model = self._local_model

        vectors: list[list[float]] = []
        for text in texts:
            result = model.create_embedding(text)  # type: ignore[union-attr]
            # llama_cpp returns {'embedding': [...], ...}
            if isinstance(result, dict) and "embedding" in result:
                emb = result["embedding"]
            elif isinstance(result, list):
                emb = result
            else:
                raise EmbeddingError(
                    f"Unexpected embedding result type: {type(result)}"
                )
            vectors.append(list(emb))

        return vectors

    # ------------------------------------------------------------------ #
    #  Provider: sentence-transformers (HuggingFace local)
    # ------------------------------------------------------------------ #

    def _ensure_st_model(self):
        """Lazy-load the sentence-transformers model."""
        if self._st_model is not None:
            return

        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            raise EmbeddingError(
                "The 'sentence-transformers' package is required. "
                "Install it with: pip install sentence-transformers"
            ) from None

        # Use HF mirror for China access, cache on D drive
        import os as _os
        if not _os.environ.get("HF_ENDPOINT"):
            _os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

        # Put model cache outside C drive
        cache_dir = self._model_path  # Allow model_path as cache_dir for ST
        if not cache_dir:
            cache_dir = _os.environ.get("HF_HOME", str(Path.home() / ".cache" / "huggingface"))
            # Default to D drive if available
            if cache_dir.startswith("C:") or cache_dir.startswith("c:"):
                alt = "D:/cache/huggingface"
                if Path(alt).parent.exists():
                    cache_dir = alt

        log.info("Loading sentence-transformers model: %s (cache: %s)", self._model, cache_dir)
        self._st_model = SentenceTransformer(self._model, cache_folder=cache_dir)
        log.info("Sentence-transformers model loaded (%d dims)", self._dims)

    def _embed_st(self, texts: list[str]) -> list[list[float]]:
        """Compute embeddings using sentence-transformers."""
        self._ensure_st_model()
        results = self._st_model.encode(texts, normalize_embeddings=True)  # type: ignore[union-attr]
        # Convert numpy.float32 → Python float for JSON serialization
        return [[float(x) for x in vec] for vec in results]

    # ------------------------------------------------------------------ #
    #  Dispatch
    # ------------------------------------------------------------------ #

    def _embed_uncached(self, texts: list[str]) -> list[list[float]]:
        """Dispatch to the active provider and cache results."""
        if self._provider == PROVIDER_OLLAMA:
            vectors = self._embed_ollama(texts)
        elif self._provider == PROVIDER_SENTENCE_TRANSFORMERS:
            vectors = self._embed_st(texts)
        elif self._provider == PROVIDER_LOCAL:
            vectors = self._embed_local(texts)
        else:
            vectors = self._embed_openai(texts)

        # Backfill cache (ensure Python floats, not numpy)
        for text, vec in zip(texts, vectors):
            py_vec = [float(x) for x in vec]
            self._store.insert_embedding_cache(
                provider=self._provider,
                model=self._model,
                provider_key=self._provider_key,
                hash=self._hash(text),
                embedding=py_vec,
                dims=len(py_vec),
            )

        return vectors

    # ------------------------------------------------------------------ #
    #  Helpers
    # ------------------------------------------------------------------ #

    def _check_cache(self, text: str) -> list[float] | None:
        return self._store.get_embedding_cache(
            provider=self._provider,
            model=self._model,
            provider_key=self._provider_key,
            hash=self._hash(text),
        )

    @staticmethod
    def _hash(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _cache_key(self) -> str:
        if self._provider == PROVIDER_LOCAL:
            # Use model path hash to differentiate between different GGUF models
            mp = self._model_path or self._model
            return hashlib.sha256(mp.encode()).hexdigest()[:8]
        if not self._api_key:
            return "local"
        return self._api_key[-8:]

    def _estimate_tokens(self, text: str) -> int:
        """Rough token count."""
        try:
            import tiktoken

            enc = tiktoken.get_encoding("cl100k_base")
            return len(enc.encode(text))
        except Exception:
            return len(text) // 4

    def _truncate_if_needed(self, text: str) -> str:
        max_tokens = (
            LOCAL_CONTEXT_SIZE if self._provider == PROVIDER_LOCAL
            else OPENAI_MAX_TOKENS
        )
        estimated = self._estimate_tokens(text)
        if estimated <= max_tokens:
            return text

        log.warning(
            "Text length ~%d tokens exceeds model limit of %d, truncating.",
            estimated,
            max_tokens,
        )
        return text[:max_tokens * 3]
