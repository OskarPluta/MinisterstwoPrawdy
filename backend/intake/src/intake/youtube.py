"""YouTube links: title, channel, description and publication date through official, legal APIs.

With YOUTUBE_API_KEY: YouTube Data API v3 `videos.list` (title, description, publishedAt,
channel; free key with a daily quota). Without it: the public oEmbed endpoint (title and channel).

Not done: the recording and its captions. `captions.download` only works for the video's owner,
and transcript scrapers use undocumented endpoints against YouTube's terms. The user is told,
and can type the claim or send a screenshot instead.
"""

from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, quote, urlsplit

from rag.attachments import Attachment

from .fetch import FetchFailed, SafeFetcher

API_URL = "https://www.googleapis.com/youtube/v3/videos?part=snippet&id={id}"
OEMBED_URL = "https://www.youtube.com/oembed?format=json&url={url}"
MAX_DESCRIPTION_CHARS = 3000

_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com",
          "youtube-nocookie.com", "www.youtube-nocookie.com"}

LIMIT_NO_RECORDING = (
    "Nie analizujemy samego nagrania ani jego napisów: YouTube nie udostępnia ich legalnie dla cudzych filmów. "
    "Jeśli twierdzenie pada w filmie, wpisz je albo prześlij zrzut ekranu."
)
LIMIT_OEMBED = "Bez klucza YouTube Data API znamy tylko tytuł i kanał filmu, bez opisu i daty publikacji."


def video_id(url: str) -> str | None:
    """The 11-character id of a YouTube video link, or None if `url` is not one."""
    try:
        p = urlsplit(url)
    except ValueError:
        return None
    host = (p.hostname or "").lower()
    vid = None
    if host == "youtu.be":
        vid = p.path.lstrip("/").split("/", 1)[0]
    elif host in _HOSTS:
        if p.path == "/watch":
            vid = (parse_qs(p.query).get("v") or [""])[0]
        else:
            m = re.match(r"^/(?:shorts|embed|live|v)/([^/?#]+)", p.path)
            vid = m.group(1) if m else None
    return vid if vid and _ID.match(vid) else None


def read_youtube(url: str, vid: str, fetcher: SafeFetcher | None, api_key: str | None) -> Attachment:
    a = Attachment(kind="youtube", label=url, meta={"video_id": vid})
    a.limitations.append(LIMIT_NO_RECORDING)
    if fetcher is None:
        a.limitations.insert(0, "Pobieranie danych o filmach nie jest skonfigurowane w tej instalacji.")
        return a
    canonical = f"https://www.youtube.com/watch?v={vid}"
    try:
        if api_key:
            # The key goes in a header (not the URL), so it never appears in logs.
            r = fetcher.get(API_URL.format(id=vid), accept="application/json",
                            private_headers={"X-Goog-Api-Key": api_key})
            items = json.loads(r.body).get("items") or []
            if not items:
                a.limitations.insert(0, "Film nie istnieje albo jest prywatny.")
                return a
            sn = items[0].get("snippet") or {}
            title, channel = sn.get("title") or "", sn.get("channelTitle") or ""
            description = (sn.get("description") or "")[:MAX_DESCRIPTION_CHARS]
            a.method = "youtube_data_api"
            if sn.get("publishedAt"):
                a.material_date, a.material_date_kind = sn["publishedAt"], "published"
        else:
            r = fetcher.get(OEMBED_URL.format(url=quote(canonical, safe="")), accept="application/json")
            data = json.loads(r.body)
            title, channel, description = data.get("title") or "", data.get("author_name") or "", ""
            a.method = "youtube_oembed"
            a.limitations.append(LIMIT_OEMBED)
    except FetchFailed as e:
        a.limitations.insert(0, f"Nie udało się pobrać danych o filmie. {e.message}")
        return a
    except (ValueError, AttributeError):
        a.limitations.insert(0, "Serwis YouTube zwrócił nieczytelne dane o filmie.")
        return a
    lines = [f"Tytuł filmu: {title}", f"Kanał: {channel}"]
    if description:
        lines.append("Opis filmu:\n" + description)
    a.text = "\n".join(lines)
    return a
