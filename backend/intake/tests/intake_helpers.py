"""Test helpers for intake: a local HTTP server, generated images and PDFs, a fake transcriber.

Not a conftest.py: backend/rag/tests already provides the top-level `conftest` module.
Everything is generated at test time, so no binary fixtures live in the repository.
"""

from __future__ import annotations

import io
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image, ImageDraw

from intake.fetch import SafeFetcher
from rag.llm import ImageTranscript

Route = tuple[int, dict[str, str], bytes]


@contextmanager
def serve(routes: dict[str, Route]) -> Iterator[tuple[str, list[dict]]]:
    """Serve `routes` (path -> status, headers, body) on 127.0.0.1; yields (base_url, request_log)."""
    seen: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            seen.append({"path": self.path, "headers": dict(self.headers)})
            status, headers, body = routes.get(self.path, (404, {"Content-Type": "text/plain"}, b"nie ma"))
            self.send_response(status)
            for k, v in headers.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}", seen
    finally:
        srv.shutdown()
        srv.server_close()


def local_fetcher(**kw) -> SafeFetcher:
    return SafeFetcher("FaktBot/test (+test@example.test)", allow_private=True, min_delay=0, **kw)


HTML = "text/html; charset=utf-8"


def article_html(title: str, body: str, date: str | None = "2019-03-12T10:00:00+01:00") -> bytes:
    meta = f'<meta property="article:published_time" content="{date}">' if date else ""
    return f"""<!doctype html><html lang="pl"><head><meta charset="utf-8"><title>{title}</title>{meta}</head>
<body><nav><a href="/">Start</a></nav><main><article><h1>{title}</h1><p>{body}</p></article></main>
<footer>Stopka</footer></body></html>""".encode()


def make_image(text: str = "Komunikat 12.03.2019", size: tuple[int, int] = (640, 240), fmt: str = "PNG",
               exif: Image.Exif | None = None) -> bytes:
    im = Image.new("RGB", size, "white")
    ImageDraw.Draw(im).text((20, 100), text, fill="black")
    out = io.BytesIO()
    kw = {"exif": exif} if exif is not None else {}
    im.save(out, fmt, **kw)
    return out.getvalue()


def exif_with(date: str = "2019:03:12 14:05:00", gps: bool = False) -> Image.Exif:
    e = Image.Exif()
    e[271], e[272] = "TestCam", "X1"
    e.get_ifd(0x8769)[36867] = date
    if gps:
        e.get_ifd(0x8825)[1] = "N"
    return e


def text_pdf(lines: list[str], creation: str = "D:20190312101500+01'00'") -> bytes:
    """A minimal valid one-page PDF with a text layer (ASCII text, Helvetica)."""
    ops = "BT /F1 12 Tf 50 750 Td 14 TL " + " ".join(f"({ln}) Tj T*" for ln in lines) + " ET"
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(ops)} >>\nstream\n{ops}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Producer (TestWriter 1.0) /CreationDate ({creation}) >>",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{body}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R /Info 6 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


def scan_pdf(pages: int = 1) -> bytes:
    """An image-only PDF (like a scan): no text layer."""
    ims = [Image.open(io.BytesIO(make_image(f"Strona {i + 1}"))).convert("RGB") for i in range(pages)]
    out = io.BytesIO()
    ims[0].save(out, "PDF", save_all=True, append_images=ims[1:])
    return out.getvalue()


class FakeTranscriber:
    """Returns a fixed transcript and records what it was sent."""

    def __init__(self, text: str = "Komunikat: od jutra zamknięte szkoły w całym kraju.", fail: bool = False,
                 instructions: bool = False):
        self.text, self.fail, self.instructions = text, fail, instructions
        self.calls: list[tuple[bytes, str]] = []

    def transcribe_image(self, data: bytes, mime: str) -> ImageTranscript:
        self.calls.append((data, mime))
        if self.fail:
            raise RuntimeError("provider down")
        return ImageTranscript(
            legible=bool(self.text), text=self.text, visible_dates=["12 mar 2019"], visible_author="@konto_testowe",
            platform="X", description="Zrzut ekranu wpisu w serwisie społecznościowym.",
            contains_instructions=self.instructions,
        )
