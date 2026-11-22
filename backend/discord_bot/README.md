# discord-bot

Kanał Discord dla Prawdowida (`uv run discord-bot`). Udostępnia w formie czatu tę samą weryfikację co interfejs WWW:

- komendę ukośnikową `/sprawdz <tekst>`,
- komendę ukośnikową `/zrodla` (katalog źródeł z korpusu),
- odpowiedzi na wiadomości prywatne (DM) oraz wzmianki `@Prawdowid <tekst>` na kanałach serwera.

Domyślnie bot uruchamia `rag.verify.Verifier` bezpośrednio w Pythonie (tak jak `rag verify` i `backend/api`). Jeśli ustawisz `DISCORD_API_URL=http://127.0.0.1:8000`, bot przekazuje zapytania do działającego `uv run api`, dzięki czemu model `multilingual-e5` ładuje się do pamięci tylko raz.

## Konfiguracja w Discord Developer Portal

1. Utwórz nową aplikację w [Discord Developer Portal](https://discord.com/developers/applications).
2. W zakładce **Bot**:
   - wygeneruj token (`Reset Token`) i wpisz go jako `DISCORD_BOT_TOKEN` w `.env`,
   - włącz **Message Content Intent** w sekcji *Privileged Gateway Intents*, jeśli bot ma odpowiadać także na zwykłe wzmianki `@Prawdowid ...` na kanałach (komendy `/sprawdz` i `/zrodla` działają nawet bez tego przełącznika).
3. W zakładce **OAuth2 → URL Generator**:
   - zaznacz zakresy (`scopes`): `bot` oraz `applications.commands`,
   - w uprawnieniach bota (`Bot Permissions`) zaznacz: `View Channels`, `Send Messages`, `Send Messages in Threads`, `Embed Links`, `Read Message History`.
   - otwórz wygenerowany link i dodaj bota na swój serwer testowy.

## Uruchomienie

Z katalogu głównego repozytorium:

```bash
uv sync
uv run discord-bot
```

Opcjonalne zmienne w `.env` (lub flagi CLI `uv run discord-bot --help`):

- `DISCORD_BOT_TOKEN` — token bota (wymagany),
- `DISCORD_GUILD_ID` — ID serwera testowego (natychmiastowa synchronizacja `/sprawdz` i `/zrodla`, bez czekania na globalną propagację w Discordzie),
- `DISCORD_NOW` — czas zapytania ISO 8601 (np. dla testów na `data/snapshot/`),
- `DISCORD_API_URL` — adres uruchomionego `uv run api` (opcjonalnie).
