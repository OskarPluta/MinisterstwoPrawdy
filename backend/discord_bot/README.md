# discord-bot

Kanał Discord dla Prawdowida (`uv run discord-bot`). Udostępnia w formie czatu tę samą weryfikację co interfejs WWW:

- komendę ukośnikową `/sprawdz <tekst>`,
- komendę ukośnikową `/zrodla` (katalog źródeł z korpusu),
- komendy `/udostepnij <identyfikator>` (link do wyniku tylko do odczytu) i `/usun <identyfikator>` (usunięcie zapisu sprawdzenia),
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
- `DISCORD_API_URL` — adres uruchomionego `uv run api` (opcjonalnie),
- `PRAWDOWID_PUBLIC_URL` — publiczny adres interfejsu WWW, np. `https://prawdowid.example.pl` (flaga `--public-url`). Bot składa z niego linki `<adres>/wynik/<share_id>`. Bez tej zmiennej bot nie tworzy linków i mówi wprost, że udostępnianie jest wyłączone. Usuwanie działa zawsze.

## Udostępnianie i usuwanie wyniku

Każde sprawdzenie zapisane w logu (`checks`, retencja `PRAWDOWID_CHECKS_RETENTION_DAYS`) ma losowy identyfikator `check_id`. To jedyny klucz do usunięcia zapisu, więc bot **nigdy nie pokazuje go na kanale publicznym**. Dostaje go tylko osoba, która zleciła sprawdzenie:

- **`/sprawdz`**: wynik trafia na kanał, a zaraz po nim przychodzi wiadomość widoczna tylko dla autora (ephemeral) z identyfikatorem i przyciskami „Utwórz link do wyniku” oraz „Usuń zapis”.
- **Wzmianka `@Prawdowid` na kanale**: pod wynikiem jest jeden przycisk „Udostępnij lub usuń”. Działa tylko dla autora wzmianki (inni dostają prywatną odmowę). Otwiera ten sam prywatny panel z identyfikatorem.
- **Wiadomość prywatna (DM)**: rozmowa jest prywatna, więc panel z identyfikatorem przychodzi jako druga wiadomość.

W panelu:

- „Utwórz link do wyniku” zwraca `Link do wyniku (tylko do odczytu, działa do DD.MM.RRRR): <adres>`. Link tworzy się tylko na wyraźne żądanie autora i tylko dla wyniku z werdyktami (nie dla błędów). Strona `/wynik/<share_id>` nie ujawnia `check_id`. Ponowne udostępnienie daje ten sam link. „Wyłącz link” go wyłącza.
- „Usuń zapis” pyta o potwierdzenie, potem usuwa zapis. Link do wyniku przestaje wtedy działać, a przycisk pod publicznym wynikiem znika.

Przyciski żyją w pamięci bota: panel przez 14 minut, przycisk pod wynikiem przez godzinę. Po restarcie bota przestają działać. Wtedy zostają komendy (odpowiedzi widzi tylko autor):

- `/usun identyfikator:<check_id>` — usuwa zapis,
- `/udostepnij identyfikator:<check_id>` — tworzy link, a z `wylacz:True` go wyłącza.

Kto zna identyfikator, może usunąć zapis (tak samo jak w interfejsie WWW). Gdy zapisu nie ma (np. przekroczony limit zapytań, awaria API albo `PRAWDOWID_CHECKS_RETENTION_DAYS=0` w trybie bezpośrednim), bot nie pokazuje identyfikatora ani przycisków.

Tryb bezpośredni woła `rag.db.share_check`, `unshare_check` i `delete_checks` pod blokadą usługi. Tryb API woła `POST`/`DELETE /api/checks/{check_id}/share` i `DELETE /api/checks/{check_id}` z nagłówkiem `X-Forwarded-For: discord:<id użytkownika>` i pokazuje komunikaty `detail` z odpowiedzi 404 i 429. Zapisy mają własny limit na użytkownika (20 na minutę, 200 na godzinę), osobny od limitu sprawdzeń.
