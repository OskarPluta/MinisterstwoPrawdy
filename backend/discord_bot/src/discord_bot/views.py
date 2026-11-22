"""Buttons for the requester of a check: share link and deletion of the check log.

Design (the check_id deletes the check, so it never reaches a public channel):
- The check_id lives only in the bot's memory (`CheckHandle`) and in messages only the requester
  sees (ephemeral messages, DMs). Button custom_ids are random, never the check_id: components
  of a public message are visible to every member's client.
- Every button checks `interaction.user.id == requester_id` and politely refuses anyone else.
- Views live in memory, so they time out and die on a bot restart. `/usun <identyfikator>` and
  `/udostepnij <identyfikator>` keep working then, which is why the private note shows the id.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import discord

from .format import (
    MSG_ALREADY_DELETED,
    MSG_CONFIRM_DELETE,
    MSG_NOT_REQUESTER,
    format_private_note,
)

if TYPE_CHECKING:
    from rag.verify import CheckResult

    from .bot import DiscordService

log = logging.getLogger(__name__)

# An ephemeral message can be edited through its interaction token for 15 minutes only.
PANEL_TIMEOUT_S = 14 * 60.0
# The button under a public result (a normal bot message, editable at any time).
OPENER_TIMEOUT_S = 60 * 60.0
DISCORD_CONTENT_MAX = 2000


def is_requester(user_id: int | None, requester_id: int) -> bool:
    """Only the person who asked for the check may share or delete it."""
    return user_id is not None and user_id == requester_id


@dataclass
class CheckHandle:
    """One logged check as the bot remembers it (in memory, for the life of its buttons)."""

    check_id: str
    requester_id: int
    shareable: bool  # an ok result with claims and a configured public URL
    sharing_enabled: bool
    share_link: str | None = None  # 'Link do wyniku (...): <url>' once shared
    deleted: bool = False
    opener: OpenerView | None = None  # the button under the public result, removed after deletion


def check_handle(res: CheckResult, requester_id: int, service: DiscordService) -> CheckHandle | None:
    """A handle for a logged check, or None when nothing was logged (no check_id): no buttons then."""
    if not res.check_id:
        return None
    result_ok = res.status == "ok" and bool(res.claims)
    return CheckHandle(
        check_id=res.check_id,
        requester_id=requester_id,
        shareable=result_ok and service.sharing_enabled,
        sharing_enabled=service.sharing_enabled,
    )


class RequesterOnlyView(discord.ui.View):
    """A view whose buttons answer only the requester; others get a polite ephemeral refusal."""

    def __init__(self, handle: CheckHandle, *, timeout: float) -> None:
        super().__init__(timeout=timeout)
        self.handle = handle
        self.message: discord.Message | discord.WebhookMessage | None = None

    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        if is_requester(getattr(interaction.user, "id", None), self.handle.requester_id):
            return True
        await interaction.response.send_message(MSG_NOT_REQUESTER, ephemeral=True)
        return False

    async def remove_buttons(self) -> None:
        self.stop()
        if self.message is None:
            return
        try:
            await self.message.edit(view=None)
        except discord.DiscordException as e:  # message gone or token expired: nothing to clean up
            log.debug("could not remove buttons: %s", e)

    async def on_timeout(self) -> None:
        await self.remove_buttons()


def _button(label: str, style: discord.ButtonStyle, callback) -> discord.ui.Button:
    # No custom_id given: discord.py makes a random one, so the check_id is never in the payload.
    btn: discord.ui.Button = discord.ui.Button(label=label, style=style)
    btn.callback = callback  # type: ignore[method-assign]
    return btn


class PanelView(RequesterOnlyView):
    """The requester's private panel: the deletion key, share link, unshare and delete."""

    def __init__(self, service: DiscordService, handle: CheckHandle, *, timeout: float = PANEL_TIMEOUT_S) -> None:
        super().__init__(handle, timeout=timeout)
        self.service = service
        self.state: Literal["idle", "confirm"] = "idle"
        self.status: str | None = None  # the last outcome, shown under the note
        self.render()

    def text(self) -> str:
        if self.handle.deleted:
            return self.status or MSG_ALREADY_DELETED
        parts = [format_private_note(self.handle.check_id, shareable=self.handle.shareable,
                                     sharing_enabled=self.handle.sharing_enabled)]
        if self.handle.share_link:
            parts.append(self.handle.share_link)
        if self.state == "confirm":
            parts.append(MSG_CONFIRM_DELETE)
        elif self.status:
            parts.append(self.status)
        return "\n\n".join(parts)[:DISCORD_CONTENT_MAX]

    def render(self) -> None:
        self.clear_items()
        if self.handle.deleted:
            return
        if self.state == "confirm":
            self.add_item(_button("Tak, usuń zapis", discord.ButtonStyle.danger, self.on_delete_confirmed))
            self.add_item(_button("Anuluj", discord.ButtonStyle.secondary, self.on_cancel))
            return
        if self.handle.shareable:
            if self.handle.share_link:
                self.add_item(_button("Wyłącz link", discord.ButtonStyle.secondary, self.on_unshare))
            else:
                self.add_item(_button("Utwórz link do wyniku", discord.ButtonStyle.primary, self.on_share))
        self.add_item(_button("Usuń zapis", discord.ButtonStyle.danger, self.on_delete))

    async def _refresh(self, interaction: discord.Interaction, *, deferred: bool) -> None:
        self.render()
        view = self if self.children else None
        if view is None:
            self.stop()
        if deferred:
            await interaction.edit_original_response(content=self.text(), view=view)
        else:
            await interaction.response.edit_message(content=self.text(), view=view)

    async def on_share(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        out = await self.service.share(self.handle.check_id, str(interaction.user.id))
        if out.ok:
            self.handle.share_link, self.status = out.message, None
        else:
            self.status = out.message
        await self._refresh(interaction, deferred=True)

    async def on_unshare(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        out = await self.service.unshare(self.handle.check_id, str(interaction.user.id))
        if out.ok:
            self.handle.share_link = None
        self.status = out.message
        await self._refresh(interaction, deferred=True)

    async def on_delete(self, interaction: discord.Interaction) -> None:
        self.state, self.status = "confirm", None
        await self._refresh(interaction, deferred=False)

    async def on_cancel(self, interaction: discord.Interaction) -> None:
        self.state = "idle"
        await self._refresh(interaction, deferred=False)

    async def on_delete_confirmed(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        out = await self.service.delete(self.handle.check_id, str(interaction.user.id))
        self.state, self.status = "idle", out.message
        if out.ok:
            self.handle.deleted, self.handle.share_link = True, None
        await self._refresh(interaction, deferred=True)
        if out.ok and self.handle.opener is not None:
            await self.handle.opener.remove_buttons()


class OpenerView(RequesterOnlyView):
    """One button under a public result: opens the requester's private panel (ephemeral)."""

    def __init__(self, service: DiscordService, handle: CheckHandle, *, timeout: float = OPENER_TIMEOUT_S) -> None:
        super().__init__(handle, timeout=timeout)
        self.service = service
        handle.opener = self
        label = "Udostępnij lub usuń" if handle.shareable else "Usuń zapis"
        self.add_item(_button(label, discord.ButtonStyle.secondary, self.on_open))

    async def on_open(self, interaction: discord.Interaction) -> None:
        if self.handle.deleted:
            await interaction.response.send_message(MSG_ALREADY_DELETED, ephemeral=True)
            return
        panel = PanelView(self.service, self.handle)
        await interaction.response.send_message(panel.text(), view=panel, ephemeral=True)
        try:
            panel.message = await interaction.original_response()
        except discord.DiscordException as e:
            log.debug("could not fetch the panel message: %s", e)
