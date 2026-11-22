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
from datetime import datetime
from typing import Any

import certifi

# NixOS / standalone uv Python builds may have no default system cafile;
# must run before importing discord/aiohttp so any cached SSL context sees it.
if ssl.get_default_verify_paths().cafile is None and not os.environ.get("SSL_CERT_FILE"):
    os.environ["SSL_CERT_FILE"] = certifi.where()

import discord
import httpx
from discord import app_commands

from rag.config import load_env
from rag.db import Connection, connect, corpus_totals, list_sources
from rag.embed import DEFAULT_EMBEDDER, Embedder, get_embedder
from rag.search import Retriever
from rag.timeutil import WARSAW, parse_ts
from rag.verify import MSG_OUTAGE, CheckResult, Verifier

from .format import (
    check_result_from_dict,
    format_check_result,
    format_sources_embed,
)

log = logging.getLogger(__name__)

EMPTY_MENTION_HINT = "Napisz twierdzenie do sprawdzenia po wzmiance lub użyj polecenia `/sprawdz`."
DEFAULT_HTTP_TIMEOUT_S = 120.0


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
    owns_conn: bool = True
    lock: threading.Lock = field(default_factory=threading.Lock)
    _verifier: Verifier | None = None

    def __post_init__(self) -> None:
        if self.api_url:
            self.api_url = self.api_url.rstrip("/")

    def verifier(self) -> Verifier:
        if self._verifier is None:
            if self.conn is None or self.embedder is None or self.llm_factory is None:
                raise RuntimeError("DiscordService missing conn, embedder, or llm_factory")
            self._verifier = Verifier(Retriever(self.conn, self.embedder), self.llm_factory())
        return self._verifier

    def _verify_via_api(self, text: str, now: datetime | None) -> CheckResult:
        payload: dict[str, Any] = {"text": text}
        if now is not None:
            payload["now"] = now.isoformat()
        try:
            with httpx.Client(timeout=DEFAULT_HTTP_TIMEOUT_S) as client:
                resp = client.post(f"{self.api_url}/api/verify", json=payload)
                resp.raise_for_status()
                return check_result_from_dict(resp.json())
        except Exception as e:  # noqa: BLE001 - outage must surface as MSG_OUTAGE, never crash bot
            log.error("API /api/verify request failed: %s", e)
            return CheckResult(check_id="", status="error", message=MSG_OUTAGE, error=repr(e))

    def verify_sync(self, text: str, now: datetime | None = None) -> CheckResult:
        if self.api_url:
            return self._verify_via_api(text, now)
        with self.lock:
            try:
                verifier = self.verifier()
            except (Exception, SystemExit) as e:  # make_llm exits when no API key is set
                log.error("cannot create LLM client: %s", e)
                return CheckResult(check_id="", status="error", message=MSG_OUTAGE, error=repr(e))
            res = verifier.verify(text, now)
        if res.error:
            log.warning("check %s: %s", res.check_id, res.error)
        return res

    async def verify(self, text: str, now: datetime | None = None) -> CheckResult:
        return await asyncio.to_thread(self.verify_sync, text, now)

    def sources_sync(self) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        if self.api_url:
            with httpx.Client(timeout=30.0) as client:
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

    def close(self) -> None:
        if self.owns_conn and self.conn is not None:
            self.conn.close()
            self.conn = None


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
            res = await self.service.verify(query, self.default_now)
        formatted = format_check_result(res)
        await message.reply(content=formatted.content, embeds=formatted.embeds, mention_author=False)

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
    """Create a configured PrawdowidBot with /sprawdz and /zrodla slash commands."""
    bot = PrawdowidBot(service, guild_id=guild_id, default_now=default_now)

    @bot.tree.command(name="sprawdz", description="Sprawdź twierdzenie lub doniesienie w oficjalnych źródłach")
    @app_commands.describe(tekst="Twierdzenie, wiadomość lub pytanie do weryfikacji")
    async def sprawdz_cmd(interaction: discord.Interaction, tekst: str) -> None:
        await interaction.response.defer(thinking=True)
        res = await service.verify(tekst, default_now)
        formatted = format_check_result(res)
        await interaction.followup.send(content=formatted.content, embeds=formatted.embeds)

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


def _default_service(api_url: str | None = None) -> DiscordService:
    resolved_api_url = (api_url or os.environ.get("DISCORD_API_URL") or "").strip() or None
    if resolved_api_url:
        log.info("Discord bot using HTTP API at %s", resolved_api_url)
        return DiscordService(api_url=resolved_api_url)

    from rag.llm import make_llm

    embedder = get_embedder(os.environ.get("PRAWDOWID_EMBEDDER") or DEFAULT_EMBEDDER)
    conn = connect()
    return DiscordService(conn=conn, embedder=embedder, llm_factory=make_llm)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="discord-bot",
        description="Uruchom bota Discorda dla Prawdowida (komenda /sprawdz, wzmianki i DM).",
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

    service = _default_service(args.api_url)
    bot = create_bot(service, guild_id=guild_id, default_now=default_now)
    bot.run(token, log_handler=None)
