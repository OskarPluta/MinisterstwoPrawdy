"""Discord bot client and service adapter for Prawdowid."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import ssl
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal

import certifi

# NixOS / standalone uv Python builds may have no default system cafile;
# must run before importing discord/aiohttp so any cached SSL context sees it.
if ssl.get_default_verify_paths().cafile is None and not os.environ.get("SSL_CERT_FILE"):
    os.environ["SSL_CERT_FILE"] = certifi.where()

import discord
import httpx
from discord import app_commands

from rag.config import load_env
from rag.db import (
    Connection,
    connect,
    corpus_totals,
    delete_checks,
    list_sources,
    share_check,
    unshare_check,
)
from rag.embed import DEFAULT_EMBEDDER, Embedder, get_embedder
from rag.ratelimit import RateLimiter
from rag.search import Retriever
from rag.timeutil import WARSAW, parse_ts
from rag.verify import MSG_OUTAGE, CheckResult, Verifier, checks_retention_days

from .format import (
    MSG_BAD_ID,
    MSG_DELETED,
    MSG_NOT_FOUND_DELETE,
    MSG_NOT_FOUND_SHARE,
    MSG_NOT_FOUND_UNSHARE,
    MSG_SHARE_OFF,
    MSG_UNSHARED,
    check_result_from_dict,
    format_check_result,
    format_share_link,
    format_sources_embed,
    parse_check_id,
    public_base_url,
    share_url,
)
from .views import OpenerView, PanelView, check_handle

log = logging.getLogger(__name__)

EMPTY_MENTION_HINT = "Napisz twierdzenie do sprawdzenia po wzmiance lub użyj polecenia `/sprawdz`."
DEFAULT_HTTP_TIMEOUT_S = 120.0
WRITE_HTTP_TIMEOUT_S = 15.0

Action = Literal["share", "unshare", "delete"]
_NOT_FOUND: dict[str, str] = {
    "share": MSG_NOT_FOUND_SHARE,
    "unshare": MSG_NOT_FOUND_UNSHARE,
    "delete": MSG_NOT_FOUND_DELETE,
}


@dataclass(frozen=True)
class ActionOutcome:
    """Result of a share / unshare / delete: what to tell the requester (Polish, plain text)."""

    ok: bool
    message: str
    url: str | None = None  # the public read-only page, for a successful share


def extract_query_from_message(content: str, bot_user_id: int | None, is_dm: bool) -> str | None:
    """Extract the claim text if a Discord message is directed at the bot.

    Returns None when the message does not address the bot (e.g. normal channel chatter),
    or the stripped text (possibly empty) when addressed via DM or @mention.
    """
    if bot_user_id is not None:
        mention_rx = re.compile(rf"<@!?{bot_user_id}>")
        if not is_dm and not mention_rx.search(content):
            return None
        return mention_rx.sub("", content).strip()
    return content.strip() if is_dm else None


@dataclass
class DiscordService:
    """Thread-safe verification adapter: wraps rag.verify.Verifier or proxies to HTTP API."""

    conn: Connection | None = None
    embedder: Embedder | None = None
    llm_factory: Callable[[], Any] | None = None
    api_url: str | None = None
    # Public base URL of the frontend (PRAWDOWID_PUBLIC_URL); without it there is no share link.
    public_url: str | None = None
    owns_conn: bool = True
    lock: threading.Lock = field(default_factory=threading.Lock)
    # Per Discord user and daily limits (rag/ratelimit.py, PRAWDOWID_RATE_*): each check costs LLM calls.
    limiter: RateLimiter = field(default_factory=RateLimiter.from_env)
    # Share / delete cost nothing but write to the database: a looser per-user brake (as in the API).
    write_limiter: RateLimiter = field(default_factory=lambda: RateLimiter(20, 200, 0))
    http_transport: httpx.BaseTransport | None = None  # tests inject httpx.MockTransport
    _verifier: Verifier | None = None

    def __post_init__(self) -> None:
        if self.api_url:
            self.api_url = self.api_url.rstrip("/")
        self.public_url = public_base_url(self.public_url)

    @property
    def sharing_enabled(self) -> bool:
        return self.public_url is not None

    def _http(self, timeout: float) -> httpx.Client:
        return httpx.Client(timeout=timeout, transport=self.http_transport)

    def verifier(self) -> Verifier:
        if self._verifier is None:
            if self.conn is None or self.embedder is None or self.llm_factory is None:
                raise RuntimeError("DiscordService missing conn, embedder, or llm_factory")
            self._verifier = Verifier(Retriever(self.conn, self.embedder), self.llm_factory())
        return self._verifier

    def _verify_via_api(self, text: str, now: datetime | None, user: str | None = None) -> CheckResult:
        payload: dict[str, Any] = {"text": text}
        if now is not None:
            payload["now"] = now.isoformat()
        # The API trusts X-Forwarded-For from a local peer, so it limits each Discord user separately.
        headers = {"X-Forwarded-For": f"discord:{user}"} if user else {}
        try:
            with self._http(DEFAULT_HTTP_TIMEOUT_S) as client:
                resp = client.post(f"{self.api_url}/api/verify", json=payload, headers=headers)
                if resp.status_code == 429:
                    detail = (resp.json() or {}).get("detail") or MSG_OUTAGE
                    return CheckResult(check_id="", status="error", message=str(detail))
                resp.raise_for_status()
                return check_result_from_dict(resp.json())
        except Exception as e:  # noqa: BLE001 - outage must surface as MSG_OUTAGE, never crash bot
            log.error("API /api/verify request failed: %s", e)
            return CheckResult(check_id="", status="error", message=MSG_OUTAGE, error=repr(e))

    def verify_sync(self, text: str, now: datetime | None = None, user: str | None = None) -> CheckResult:
        refused = self.limiter.hit(f"discord:{user or 'anon'}")
        if refused:
            log.warning("rate limit (%s) for a Discord user, retry after %d s", refused.scope, refused.retry_after)
            return CheckResult(check_id="", status="error", message=refused.message)
        if self.api_url:
            return self._verify_via_api(text, now, user)
        with self.lock:
            try:
                verifier = self.verifier()
            except (Exception, SystemExit) as e:  # make_llm exits when no API key is set
                log.error("cannot create LLM client: %s", e)
                return CheckResult(check_id="", status="error", message=MSG_OUTAGE, error=repr(e))
            res = verifier.verify(text, now)
        if res.error:
            log.warning("check %s: %s", res.check_id, res.error)
        if not getattr(verifier, "record", True):
            res.check_id = ""  # retention 0: nothing was logged, so there is nothing to share or delete
        return res

    async def verify(self, text: str, now: datetime | None = None, user: str | None = None) -> CheckResult:
        return await asyncio.to_thread(self.verify_sync, text, now, user)

    def sources_sync(self) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        if self.api_url:
            with self._http(30.0) as client:
                resp = client.get(f"{self.api_url}/api/sources")
                resp.raise_for_status()
                data = resp.json()
                return dict(data["totals"]), list(data["sources"])
        if self.conn is None:
            raise RuntimeError("DiscordService missing conn")
        with self.lock:
            return corpus_totals(self.conn), list_sources(self.conn)

    async def sources(self) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        return await asyncio.to_thread(self.sources_sync)

    # --- the check log: share (public read-only link), unshare, delete -----------------------

    def write_sync(self, action: Action, raw_check_id: str, user: str | None = None) -> ActionOutcome:
        """Share, unshare or delete the logged check `raw_check_id` on behalf of Discord `user`."""
        check_id = parse_check_id(raw_check_id)
        if check_id is None:
            return ActionOutcome(False, MSG_BAD_ID)
        if action == "share" and not self.sharing_enabled:
            return ActionOutcome(False, MSG_SHARE_OFF)  # never hand out a link that leads nowhere
        refused = self.write_limiter.hit(f"discord:{user or 'anon'}")
        if refused:
            log.warning("write rate limit (%s) for a Discord user, retry after %d s", refused.scope, refused.retry_after)
            return ActionOutcome(False, refused.message)
        try:
            if self.api_url:
                return self._write_via_api(action, check_id, user)
            return self._write_direct(action, check_id)
        except Exception as e:  # noqa: BLE001 - an outage is a message, never a crashed bot
            log.error("%s of a check failed: %r", action, e)
            return ActionOutcome(False, MSG_OUTAGE)

    def _write_direct(self, action: Action, check_id: str) -> ActionOutcome:
        if self.conn is None:
            raise RuntimeError("DiscordService missing conn")
        with self.lock:
            if action == "share":
                row = share_check(self.conn, check_id)
            elif action == "unshare":
                row = {} if unshare_check(self.conn, check_id) else None
            else:
                row = {} if delete_checks(self.conn, [check_id]) else None
        if row is None:
            return ActionOutcome(False, _NOT_FOUND[action])
        if action == "share":
            return self._shared(f"/wynik/{row['share_id']}", _expires_at(row.get("created_at")))
        return ActionOutcome(True, MSG_UNSHARED if action == "unshare" else MSG_DELETED)

    def _write_via_api(self, action: Action, check_id: str, user: str | None) -> ActionOutcome:
        method = "POST" if action == "share" else "DELETE"
        suffix = "" if action == "delete" else "/share"
        headers = {"X-Forwarded-For": f"discord:{user}"} if user else {}
        with self._http(WRITE_HTTP_TIMEOUT_S) as client:
            resp = client.request(method, f"{self.api_url}/api/checks/{check_id}{suffix}", headers=headers)
        if resp.status_code in (404, 429):
            detail = _detail(resp)
            return ActionOutcome(False, detail or (_NOT_FOUND[action] if resp.status_code == 404 else MSG_OUTAGE))
        resp.raise_for_status()
        if action == "share":
            data = resp.json()
            return self._shared(str(data.get("path") or ""), data.get("expires_at"))
        return ActionOutcome(True, MSG_UNSHARED if action == "unshare" else MSG_DELETED)

    def _shared(self, path: str, expires_at: str | None) -> ActionOutcome:
        url = share_url(self.public_url, path)
        if url is None:
            raise ValueError(f"unexpected share path {path!r}")
        return ActionOutcome(True, format_share_link(url, expires_at), url=url)

    def share_sync(self, check_id: str, user: str | None = None) -> ActionOutcome:
        return self.write_sync("share", check_id, user)

    def unshare_sync(self, check_id: str, user: str | None = None) -> ActionOutcome:
        return self.write_sync("unshare", check_id, user)

    def delete_sync(self, check_id: str, user: str | None = None) -> ActionOutcome:
        return self.write_sync("delete", check_id, user)

    async def share(self, check_id: str, user: str | None = None) -> ActionOutcome:
        return await asyncio.to_thread(self.write_sync, "share", check_id, user)

    async def unshare(self, check_id: str, user: str | None = None) -> ActionOutcome:
        return await asyncio.to_thread(self.write_sync, "unshare", check_id, user)

    async def delete(self, check_id: str, user: str | None = None) -> ActionOutcome:
        return await asyncio.to_thread(self.write_sync, "delete", check_id, user)

    def close(self) -> None:
        if self.owns_conn and self.conn is not None:
            self.conn.close()
            self.conn = None


def _expires_at(created_at: str | None) -> str | None:
    """When the share stops working: the check log's retention (same rule as the API)."""
    days = checks_retention_days()
    start = parse_ts(created_at)
    return (start + timedelta(days=days)).isoformat().replace("+00:00", "Z") if start and days else None


def _detail(resp: httpx.Response) -> str | None:
    try:
        detail = (resp.json() or {}).get("detail")
    except ValueError:
        return None
    return detail if isinstance(detail, str) and detail.strip() else None


class PrawdowidBot(discord.Client):
    """Discord Gateway client handling slash commands, DMs, and @mentions."""

    def __init__(
        self,
        service: DiscordService,
        *,
        guild_id: int | None = None,
        default_now: datetime | None = None,
        intents: discord.Intents | None = None,
    ) -> None:
        if intents is None:
            intents = discord.Intents.default()
            intents.message_content = True
        super().__init__(intents=intents)
        self.service = service
        self.guild_id = guild_id
        self.default_now = default_now
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self) -> None:
        if self.guild_id is not None:
            guild = discord.Object(id=self.guild_id)
            self.tree.copy_global_to(guild=guild)
            try:
                await self.tree.sync(guild=guild)
                log.info("Synced slash commands to guild %s", self.guild_id)
                return
            except discord.DiscordException as e:
                log.warning(
                    "Could not sync slash commands to guild %s (%s); falling back to global sync",
                    self.guild_id,
                    e,
                )
        await self.tree.sync()
        log.info("Synced global slash commands")

    async def on_ready(self) -> None:
        log.info("Discord bot logged in as %s", self.user)

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        is_dm = isinstance(message.channel, discord.DMChannel)
        bot_id = self.user.id if self.user else None
        query = extract_query_from_message(message.content, bot_id, is_dm=is_dm)
        if query is None:
            return
        if not query:
            await message.reply(EMPTY_MENTION_HINT, mention_author=False)
            return
        async with message.channel.typing():
            res = await self.service.verify(query, self.default_now, str(message.author.id))
        formatted = format_check_result(res)
        handle = check_handle(res, message.author.id, self.service)
        if handle is None:
            await message.reply(content=formatted.content, embeds=formatted.embeds, mention_author=False)
        elif is_dm:
            # A DM is private: the result, then the panel with the deletion key, as a second message.
            await message.reply(content=formatted.content, embeds=formatted.embeds, mention_author=False)
            panel = PanelView(self.service, handle)
            panel.message = await message.channel.send(panel.text(), view=panel)
        else:
            # A channel is public: one requester-only button that opens the private panel.
            opener = OpenerView(self.service, handle)
            opener.message = await message.reply(
                content=formatted.content, embeds=formatted.embeds, view=opener, mention_author=False
            )

    async def close(self) -> None:
        try:
            self.service.close()
        finally:
            await super().close()


def create_bot(
    service: DiscordService,
    *,
    guild_id: int | None = None,
    default_now: datetime | None = None,
) -> PrawdowidBot:
    """Create a configured PrawdowidBot with /sprawdz, /zrodla, /usun and /udostepnij slash commands."""
    bot = PrawdowidBot(service, guild_id=guild_id, default_now=default_now)

    @bot.tree.command(name="sprawdz", description="Sprawdź twierdzenie lub doniesienie w oficjalnych źródłach")
    @app_commands.describe(tekst="Twierdzenie, wiadomość lub pytanie do weryfikacji")
    async def sprawdz_cmd(interaction: discord.Interaction, tekst: str) -> None:
        await interaction.response.defer(thinking=True)
        res = await service.verify(tekst, default_now, str(interaction.user.id))
        formatted = format_check_result(res)
        await interaction.followup.send(content=formatted.content, embeds=formatted.embeds)
        handle = check_handle(res, interaction.user.id, service)
        if handle is not None:
            # The deletion key goes only to the requester, in an ephemeral follow-up.
            panel = PanelView(service, handle)
            panel.message = await interaction.followup.send(panel.text(), view=panel, ephemeral=True, wait=True)

    @bot.tree.command(name="usun", description="Usuń zapis swojego sprawdzenia (i link do wyniku)")
    @app_commands.describe(identyfikator="Identyfikator sprawdzenia z prywatnej wiadomości bota")
    async def usun_cmd(interaction: discord.Interaction, identyfikator: str) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        out = await service.delete(identyfikator, str(interaction.user.id))
        await interaction.followup.send(out.message, ephemeral=True)

    @bot.tree.command(name="udostepnij", description="Utwórz link do wyniku (tylko do odczytu) albo go wyłącz")
    @app_commands.describe(
        identyfikator="Identyfikator sprawdzenia z prywatnej wiadomości bota",
        wylacz="Wyłącz wcześniej utworzony link",
    )
    async def udostepnij_cmd(interaction: discord.Interaction, identyfikator: str, wylacz: bool = False) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        user = str(interaction.user.id)
        out = await (service.unshare(identyfikator, user) if wylacz else service.share(identyfikator, user))
        await interaction.followup.send(out.message, ephemeral=True)

    @bot.tree.command(name="zrodla", description="Pokaż katalog źródeł używanych przez Prawdowida")
    async def zrodla_cmd(interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        try:
            totals, sources = await service.sources()
            embed = format_sources_embed(totals, sources)
            await interaction.followup.send(embed=embed)
        except Exception as e:  # noqa: BLE001
            log.error("Failed to fetch sources: %s", e)
            err = format_check_result(CheckResult(check_id="", status="error", message=MSG_OUTAGE))
            await interaction.followup.send(embeds=err.embeds)

    return bot


def _default_service(api_url: str | None = None, public_url: str | None = None) -> DiscordService:
    raw_public = (public_url or os.environ.get("PRAWDOWID_PUBLIC_URL") or "").strip()
    resolved_public = public_base_url(raw_public)
    if raw_public and resolved_public is None:
        log.warning("PRAWDOWID_PUBLIC_URL=%r is not an http(s) address; sharing results is off", raw_public)
    elif resolved_public is None:
        log.info("PRAWDOWID_PUBLIC_URL not set; sharing results is off")

    resolved_api_url = (api_url or os.environ.get("DISCORD_API_URL") or "").strip() or None
    if resolved_api_url:
        log.info("Discord bot using HTTP API at %s", resolved_api_url)
        return DiscordService(api_url=resolved_api_url, public_url=resolved_public)

    from rag.llm import make_llm

    embedder = get_embedder(os.environ.get("PRAWDOWID_EMBEDDER") or DEFAULT_EMBEDDER)
    conn = connect()
    return DiscordService(conn=conn, embedder=embedder, llm_factory=make_llm, public_url=resolved_public)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="discord-bot",
        description="Uruchom bota Discorda dla Prawdowida (/sprawdz, /zrodla, /usun, /udostepnij, wzmianki i DM).",
    )
    parser.add_argument(
        "--guild-id",
        type=int,
        default=None,
        help="ID serwera Discorda do natychmiastowej synchronizacji komend (domyślnie DISCORD_GUILD_ID).",
    )
    parser.add_argument(
        "--now",
        default=None,
        help="Czas zapytania ISO 8601 do demo na starym zrzucie (domyślnie DISCORD_NOW lub teraz).",
    )
    parser.add_argument(
        "--api-url",
        default=None,
        help="Opcjonalny adres API HTTP (np. http://127.0.0.1:8000; domyślnie DISCORD_API_URL).",
    )
    parser.add_argument(
        "--public-url",
        default=None,
        help="Publiczny adres interfejsu WWW do linków /wynik/... (domyślnie PRAWDOWID_PUBLIC_URL).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    load_env()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    token = (os.environ.get("DISCORD_BOT_TOKEN") or "").strip()
    if not token:
        print(
            "Błąd: brak zmiennej DISCORD_BOT_TOKEN (ustaw w .env lub w środowisku).",
            file=sys.stderr,
        )
        raise SystemExit(2)

    guild_id = args.guild_id
    if guild_id is None:
        raw_guild = (os.environ.get("DISCORD_GUILD_ID") or "").strip()
        if raw_guild:
            try:
                guild_id = int(raw_guild)
            except ValueError:
                print(f"Błąd: nieprawidłowe DISCORD_GUILD_ID={raw_guild!r}", file=sys.stderr)
                raise SystemExit(2) from None

    default_now: datetime | None = None
    raw_now = args.now or (os.environ.get("DISCORD_NOW") or "").strip() or None
    if raw_now:
        default_now = parse_ts(raw_now, naive_tz=WARSAW)
        if default_now is None:
            print(f"Błąd: nieprawidłowy czas ISO 8601 w --now/DISCORD_NOW: {raw_now!r}", file=sys.stderr)
            raise SystemExit(2)

    service = _default_service(args.api_url, args.public_url)
    bot = create_bot(service, guild_id=guild_id, default_now=default_now)
    bot.run(token, log_handler=None)
