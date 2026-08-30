"""Gemini JSON LLM client with audit logging and a fake client for tests."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from app.pipeline.io import append_jsonl
from app.pipeline.models import LlmCallLog

MODEL = "gemini-3.7-flash"
PROVIDER = "vertex"


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]


class LlmClient(Protocol):
    def generate_json(self, prompt: str) -> Any: ...


class VertexJsonClient:
    """Live Vertex / Gemini JSON generator."""

    def __init__(self, model: str = MODEL) -> None:
        self.model = model
        self.provider = PROVIDER

    def generate_json(self, prompt: str) -> Any:
        from google import genai
        from google.genai import types

        project = os.environ.get("GOOGLE_CLOUD_PROJECT")
        location = os.environ.get("GOOGLE_CLOUD_LOCATION", "global")
        use_vertex = os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "true").lower() in (
            "true",
            "1",
            "yes",
        )
        if use_vertex:
            client = genai.Client(
                vertexai=True,
                project=project,
                location=location,
            )
        else:
            client = genai.Client()

        response = client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.2,
            ),
        )
        text = response.text or "null"
        return json.loads(text)


class FakeJsonClient:
    """Deterministic client for unit tests. Handler maps prompt substring -> payload."""

    def __init__(
        self,
        handler: Callable[[str], Any] | None = None,
        *,
        responses: list[Any] | None = None,
    ) -> None:
        self.model = "fake-model"
        self.provider = "fake"
        self._handler = handler
        self._responses = list(responses or [])
        self.calls: list[str] = []

    def generate_json(self, prompt: str) -> Any:
        self.calls.append(prompt)
        if self._handler is not None:
            return self._handler(prompt)
        if not self._responses:
            raise RuntimeError("FakeJsonClient has no remaining responses")
        return self._responses.pop(0)


class LlmRecorder:
    def __init__(
        self,
        client: LlmClient,
        *,
        log_path: Path,
        provider: str | None = None,
        model: str | None = None,
    ) -> None:
        self.client = client
        self.log_path = log_path
        self.provider = provider or getattr(client, "provider", PROVIDER)
        self.model = model or getattr(client, "model", MODEL)

    def call(
        self,
        *,
        stage: str,
        prompt: str,
        input_artifacts: list[str],
        output_artifact: str,
        incident_id: str | None = None,
        few_shot_examples_included: bool = False,
    ) -> Any:
        payload = self.client.generate_json(prompt)
        record = LlmCallLog(
            stage=stage,
            incident_id=incident_id,
            timestamp=utc_now(),
            provider=self.provider,
            model=self.model,
            prompt_hash=prompt_hash(prompt),
            input_artifacts=input_artifacts,
            output_artifact=output_artifact,
            few_shot_examples_included=few_shot_examples_included,
        )
        append_jsonl(self.log_path, record.model_dump(mode="json"))
        return payload
