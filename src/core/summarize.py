from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

DEFAULT_MODEL = "Qwen/Qwen2.5-1.5B-Instruct"

_SYSTEM_TEMPLATE = (
    "You are a technical-standards summarizer. In {n} sentences, describe what "
    "this specification covers, its scope, and its primary use cases. "
    "No marketing language. No bullet points."
)

_MAP_SYSTEM = (
    "You are a technical-standards summarizer. In 1-2 sentences, summarize the "
    "following excerpt. No marketing language. No bullet points."
)


@dataclass
class SummaryResult:
    text: str
    model: str
    method: str
    input_chars: int
    input_sha256: str


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _split_windows(text: str, chunk_chars: int) -> list[str]:
    if chunk_chars <= 0:
        raise ValueError("chunk_chars must be positive")
    n = len(text)
    if n <= chunk_chars:
        return [text]
    windows: list[str] = []
    start = 0
    while start < n:
        end = min(start + chunk_chars, n)
        windows.append(text[start:end])
        if end >= n:
            break
        start = end
    return windows


def _clean_summary(raw: str) -> str:
    text = raw.strip()
    text = re.sub(r"^```(?:\w+)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


class Summarizer:
    """Lazy-loaded local instruct model for document summaries."""

    def __init__(self, model_id: str = DEFAULT_MODEL) -> None:
        self.model_id = model_id
        self._tokenizer: Any = None
        self._model: Any = None

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
        except ImportError as exc:
            raise SystemExit(
                "transformers and torch are required for --summarize. "
                "Install with: pip install transformers accelerate"
            ) from exc

        print(
            f"Loading summarization model {self.model_id!r} "
            "(first run may download ~3 GB)..."
        )
        self._tokenizer = AutoTokenizer.from_pretrained(self.model_id)
        self._model = AutoModelForCausalLM.from_pretrained(
            self.model_id,
            torch_dtype="auto",
            device_map="auto",
        )
        if self._tokenizer.pad_token is None:
            self._tokenizer.pad_token = self._tokenizer.eos_token
        print(f"Summarization model ready: {self.model_id}")

    def _generate(self, messages: list[dict[str, str]], *, max_new_tokens: int = 180) -> str:
        self._ensure_loaded()
        assert self._tokenizer is not None
        assert self._model is not None

        prompt = self._tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
        )
        inputs = self._tokenizer(prompt, return_tensors="pt")
        inputs = {k: v.to(self._model.device) for k, v in inputs.items()}

        import torch

        with torch.inference_mode():
            output_ids = self._model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                do_sample=False,
                temperature=0.0,
                pad_token_id=self._tokenizer.pad_token_id,
            )

        new_tokens = output_ids[0, inputs["input_ids"].shape[1] :]
        decoded = self._tokenizer.decode(new_tokens, skip_special_tokens=True)
        return _clean_summary(decoded)

    def _user_content(
        self,
        text: str,
        *,
        title: str | None,
        authority: str | None,
    ) -> str:
        header_parts: list[str] = []
        if title:
            header_parts.append(f"Title: {title}")
        if authority:
            header_parts.append(f"Authority: {authority}")
        header = "\n".join(header_parts)
        if header:
            return f"{header}\n\n{text}"
        return text

    def summarize_pass(
        self,
        text: str,
        *,
        title: str | None = None,
        authority: str | None = None,
        target_sentences: int = 3,
        max_new_tokens: int = 180,
    ) -> str:
        messages = [
            {
                "role": "system",
                "content": _SYSTEM_TEMPLATE.format(n=target_sentences),
            },
            {
                "role": "user",
                "content": self._user_content(text, title=title, authority=authority),
            },
        ]
        return self._generate(messages, max_new_tokens=max_new_tokens)

    def summarize_map_chunk(
        self,
        text: str,
        *,
        title: str | None = None,
        authority: str | None = None,
    ) -> str:
        messages = [
            {"role": "system", "content": _MAP_SYSTEM},
            {
                "role": "user",
                "content": self._user_content(text, title=title, authority=authority),
            },
        ]
        return self._generate(messages, max_new_tokens=120)


_summarizer_cache: dict[str, Summarizer] = {}


def get_summarizer(model_id: str = DEFAULT_MODEL) -> Summarizer:
    if model_id not in _summarizer_cache:
        _summarizer_cache[model_id] = Summarizer(model_id)
    return _summarizer_cache[model_id]


def summarize_text(
    text: str,
    *,
    title: str | None = None,
    authority: str | None = None,
    model_id: str = DEFAULT_MODEL,
    max_input_chars: int = 60_000,
    chunk_chars: int = 6_000,
    target_sentences: int = 3,
    summarizer: Summarizer | None = None,
) -> SummaryResult:
    if not text.strip():
        raise ValueError("cannot summarize empty text")

    engine = summarizer or get_summarizer(model_id)
    input_sha = sha256_text(text)
    input_chars = len(text)

    if input_chars <= max_input_chars:
        summary = engine.summarize_pass(
            text,
            title=title,
            authority=authority,
            target_sentences=target_sentences,
        )
        method = "single_pass_v1"
    else:
        windows = _split_windows(text, chunk_chars)
        partials: list[str] = []
        for i, window in enumerate(windows, 1):
            print(f"  map {i}/{len(windows)} ({len(window)} chars)...")
            partials.append(
                engine.summarize_map_chunk(
                    window,
                    title=title,
                    authority=authority,
                )
            )
        combined = "\n\n".join(f"Section {i}: {p}" for i, p in enumerate(partials, 1))
        summary = engine.summarize_pass(
            combined,
            title=title,
            authority=authority,
            target_sentences=target_sentences,
            max_new_tokens=220,
        )
        method = "map_reduce_v1"

    return SummaryResult(
        text=summary,
        model=engine.model_id,
        method=method,
        input_chars=input_chars,
        input_sha256=input_sha,
    )
