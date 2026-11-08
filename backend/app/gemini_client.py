from functools import lru_cache

from google import genai

from app.config import settings


@lru_cache(maxsize=2)
def _client(location: str) -> genai.Client:
    if not settings.gcp_project:
        raise ValueError("Set GCP_PROJECT in .env before using Vertex AI.")
    return genai.Client(vertexai=True, project=settings.gcp_project, location=location)


def client() -> genai.Client:
    return _client(settings.generation_location)


def embed(text: str, task_type: str) -> list[float]:
    result = _client(settings.gcp_location).models.embed_content(
        model=settings.embedding_model,
        contents=text,
        config={"task_type": task_type, "output_dimensionality": settings.embedding_dimensions},
    )
    if not result.embeddings or not result.embeddings[0].values:
        raise ValueError("The embedding model did not return a vector.")
    vector = list(result.embeddings[0].values)
    if len(vector) != settings.embedding_dimensions:
        raise ValueError("Embedding dimension does not match the configured database schema.")
    return vector
