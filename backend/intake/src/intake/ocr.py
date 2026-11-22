"""Reading text from an image: a vision LLM (default) or local Tesseract.

PRAWDOWID_OCR = llm (default; falls back to Tesseract when installed) | tesseract | none.
The LLM sends the image to the configured provider (OpenAI or Anthropic, outside the EEA);
Tesseract runs locally (`tesseract` binary with the `pol` language pack). A missing engine
or a failed call is reported as a limitation, never as a result.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any, Protocol

log = logging.getLogger(__name__)

TESSERACT_TIMEOUT_S = 30


class OcrUnavailable(Exception):
    """`message` is a Polish limitation for the user."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


@dataclass
class OcrResult:
    text: str
    method: str  # ocr_llm | ocr_tesseract
    description: str | None = None
    visible_dates: list[str] = field(default_factory=list)
    visible_author: str | None = None
    platform: str | None = None
    contains_instructions: bool = False
    limitations: list[str] = field(default_factory=list)


class Ocr(Protocol):
    def read(self, png: bytes) -> OcrResult: ...


class Transcriber(Protocol):
    """rag.llm._StructuredLLM.transcribe_image."""

    def transcribe_image(self, data: bytes, mime: str) -> Any: ...


class LlmOcr:
    def __init__(self, transcriber: Transcriber):
        self.transcriber = transcriber

    def read(self, png: bytes) -> OcrResult:
        try:
            t = self.transcriber.transcribe_image(png, "image/png")
        except Exception as e:  # noqa: BLE001 - outage, refusal, bad output: a limitation
            log.warning("image transcription failed: %r", e)
            raise OcrUnavailable("Usługa odczytu tekstu z obrazu jest chwilowo niedostępna.") from None
        return OcrResult(
            text=t.text if t.legible else "", method="ocr_llm", description=t.description,
            visible_dates=list(t.visible_dates), visible_author=t.visible_author, platform=t.platform,
            contains_instructions=t.contains_instructions,
        )


class TesseractOcr:
    def __init__(self, binary: str | None = None, langs: str = "pol+eng"):
        self.binary = binary or shutil.which("tesseract")
        self.langs = langs

    @property
    def available(self) -> bool:
        return self.binary is not None

    def read(self, png: bytes) -> OcrResult:
        if not self.binary:
            raise OcrUnavailable("Lokalny odczyt tekstu (Tesseract) nie jest zainstalowany.")
        try:
            p = subprocess.run(
                [self.binary, "stdin", "stdout", "-l", self.langs], input=png, capture_output=True,
                timeout=TESSERACT_TIMEOUT_S, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            log.warning("tesseract failed: %r", e)
            raise OcrUnavailable("Lokalny odczyt tekstu z obrazu nie powiódł się.") from None
        if p.returncode != 0:
            log.warning("tesseract exit %s: %s", p.returncode, p.stderr[:300])
            raise OcrUnavailable("Lokalny odczyt tekstu z obrazu nie powiódł się.")
        return OcrResult(text=p.stdout.decode("utf-8", errors="replace").strip(), method="ocr_tesseract")


class ChainOcr:
    """Tries engines in order; a fallback says so in its limitations."""

    def __init__(self, engines: list[Ocr]):
        self.engines = engines

    def read(self, png: bytes) -> OcrResult:
        if not self.engines:
            raise OcrUnavailable("Odczyt tekstu z obrazów jest wyłączony w tej instalacji.")
        failures: list[str] = []
        for engine in self.engines:
            try:
                res = engine.read(png)
            except OcrUnavailable as e:
                failures.append(e.message)
                continue
            if failures:
                res.limitations.append(" ".join(failures) + " Użyliśmy prostszego, lokalnego odczytu.")
            return res
        raise OcrUnavailable(" ".join(dict.fromkeys(failures)))


def make_ocr(transcriber: Transcriber | None, mode: str | None = None) -> ChainOcr:
    mode = (mode or os.environ.get("PRAWDOWID_OCR") or "llm").lower()
    tess = TesseractOcr()
    if mode == "none":
        return ChainOcr([])
    if mode == "tesseract":
        return ChainOcr([tess])
    engines: list[Ocr] = [LlmOcr(transcriber)] if transcriber is not None else []
    if tess.available:
        engines.append(tess)
    return ChainOcr(engines)
