"""Images and PDFs: validation, metadata, OCR through a fake transcriber (no network, no LLM)."""

from __future__ import annotations

import io
import shutil

import pytest
from intake_helpers import FakeTranscriber, exif_with, make_image, scan_pdf, text_pdf
from PIL import Image

from intake.core import Intake, Upload, _safe_label
from intake.image import LIMIT_OCR, LIMIT_SCOPE, MAX_SIDE, NOTE_GPS, NOTE_NO_EXIF, read_image
from intake.ocr import ChainOcr, OcrUnavailable, TesseractOcr, make_ocr
from intake.pdf import read_pdf


def llm_ocr(tr: FakeTranscriber | None = None):
    return make_ocr(tr or FakeTranscriber(), "llm")


def test_screenshot_is_transcribed_with_stated_limits():
    tr = FakeTranscriber()
    a = read_image(make_image(size=(3000, 1000)), "zrzut.png", llm_ocr(tr))
    assert a.kind == "image" and a.method == "ocr_llm" and a.readable
    assert "Tekst z obrazu:\nKomunikat: od jutra zamknięte szkoły" in a.text
    assert "Widoczne daty: 12 mar 2019" in a.text and "Widoczny autor: @konto_testowe" in a.text
    assert "odczyt modelu, nie fakt" in a.text
    assert LIMIT_OCR in a.limitations and LIMIT_SCOPE in a.limitations
    assert NOTE_NO_EXIF in a.notes
    data, mime = tr.calls[0]
    sent = Image.open(io.BytesIO(data))
    assert mime == "image/png" and sent.format == "PNG" and max(sent.size) <= MAX_SIDE
    assert a.meta["mime"] == "image/png" and len(a.meta["sha256"]) == 64 and len(a.meta["dhash"]) == 16


def test_exif_date_and_gps_are_reported_but_not_sent():
    tr = FakeTranscriber()
    a = read_image(make_image(fmt="JPEG", exif=exif_with(gps=True)), "foto.jpg", llm_ocr(tr))
    assert a.material_date and a.material_date.startswith("2019-03-12T14:05:00") and a.material_date_kind == "taken"
    assert any("TestCam X1" in n and "12.03.2019" in n for n in a.notes)
    assert NOTE_GPS in a.notes
    sent = Image.open(io.BytesIO(tr.calls[0][0]))
    assert not sent.getexif()  # re-encoded: no EXIF, no GPS reach the model


def test_ocr_outage_is_a_limitation_not_content():
    a = read_image(make_image(), "x.png", llm_ocr(FakeTranscriber(fail=True)))
    assert not a.readable and a.method == ""
    assert a.limitations[0].startswith("Nie udało się odczytać tekstu z obrazu.")


def test_image_without_text():
    a = read_image(make_image(text=""), "x.png", llm_ocr(FakeTranscriber(text="")))
    assert "Na obrazie nie znaleźliśmy czytelnego tekstu." in a.limitations
    assert "Tekst z obrazu" not in a.text and a.text.startswith("Opis obrazu")


def test_instructions_in_image_are_flagged():
    a = read_image(make_image(), "x.png", llm_ocr(FakeTranscriber(instructions=True)))
    assert a.meta["contains_instructions"] is True


def test_too_many_pixels_and_too_many_bytes():
    im = Image.new("1", (7000, 6000))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    a = read_image(buf.getvalue(), "duzy.png", llm_ocr())
    assert "rozdzielczość" in a.limitations[0]
    b = read_image(b"\x89PNG" + b"0" * 10_000_001, "x.png", llm_ocr())
    assert "za duży" in b.limitations[0]


def test_ocr_disabled_and_chain_fallback():
    with pytest.raises(OcrUnavailable):
        make_ocr(None, "none").read(make_image())

    class Broken:
        def read(self, png):
            raise OcrUnavailable("Pierwszy silnik nie działa.")

    class Works:
        def read(self, png):
            from intake.ocr import OcrResult
            return OcrResult(text="tekst", method="ocr_tesseract")

    r = ChainOcr([Broken(), Works()]).read(b"")
    assert r.text == "tekst" and "lokalnego odczytu" in r.limitations[0]


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract not installed")
def test_tesseract_reads_text():
    r = TesseractOcr().read(make_image("KOMUNIKAT TESTOWY", size=(900, 200)))
    assert "KOMUNIKAT" in r.text.upper()


def test_missing_tesseract_is_a_limitation():
    t = TesseractOcr()
    t.binary = None
    with pytest.raises(OcrUnavailable):
        t.read(b"")


# ---------------- PDF ----------------

def test_pdf_text_layer_and_metadata():
    a = read_pdf(text_pdf(["Komunikat Wojewody nr 7", "Zakaz wstepu do lasow od 12 marca 2019"] * 4), "k.pdf", llm_ocr())
    assert a.kind == "pdf" and a.method == "pdf_text" and "[strona 1]" in a.text and "Zakaz wstepu" in a.text
    assert a.material_date and a.material_date.startswith("2019-03-12") and a.material_date_kind == "created"
    assert any("TestWriter 1.0" in n for n in a.notes)
    assert a.meta["pages"] == 1


def test_scanned_pdf_goes_through_ocr():
    tr = FakeTranscriber()
    a = read_pdf(scan_pdf(pages=5), "skan.pdf", llm_ocr(tr))
    assert a.method == "ocr_llm" and a.readable and "[strona 1]" in a.text
    assert len(tr.calls) == 3  # first OCR_PAGES pages only
    assert any("to skan" in n and "pierwsze 3 z 5" in n for n in a.limitations)


def test_scanned_pdf_with_ocr_outage():
    a = read_pdf(scan_pdf(), "skan.pdf", llm_ocr(FakeTranscriber(fail=True)))
    assert not a.readable and any("to skan" in n for n in a.limitations)


def test_broken_pdf():
    a = read_pdf(b"%PDF-1.4\nto nie jest pdf", "zly.pdf", llm_ocr())
    assert not a.readable and "nie da się odczytać" in a.limitations[0]


# ---------------- Intake.read_file / collect ----------------

def test_read_file_sniffs_content_not_name():
    intake = Intake(None, ocr_mode="llm")
    assert intake.read_file(Upload("udaje.pdf", make_image()), FakeTranscriber()).kind == "image"
    assert intake.read_file(Upload("obraz.png", text_pdf(["x" * 120])), FakeTranscriber()).kind == "pdf"
    other = intake.read_file(Upload("skrypt.svg", b"<svg onload=alert(1)>"), FakeTranscriber())
    assert not other.readable and "nie jest obsługiwany" in other.limitations[0]


def test_collect_caps_files_and_cleans_labels():
    intake = Intake(None, ocr_mode="llm")
    files = [Upload(f"../../etc/{i}.png", make_image()) for i in range(5)]
    atts = intake.collect("Sprawdź https://example.test/a", files, transcriber=FakeTranscriber())
    assert [a.kind for a in atts] == ["url", "image", "image", "image"]
    assert atts[1].label == "0.png"
    assert _safe_label("C:\\Users\\x\\zdj\u0007ecie.jpg") == "zdjecie.jpg"
