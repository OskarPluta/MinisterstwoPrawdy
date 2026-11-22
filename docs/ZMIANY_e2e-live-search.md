# Zmiany na gałęzi `e2e-live-search`

Stan na 22.11.2026, względem `main`: 42 pliki, ok. 2250 linii, 121 testów przechodzi. `main` jest nietknięty.

## Po co

System nie znajdował rzeczy, które powinien znaleźć:

- Komunikat premiera „Rosyjskie obiekty naruszyły polską przestrzeń powietrzną…” nie trafił do bazy, bo crawler nie zdążył go pobrać.
- Tekst z EUvsDisinfo dostawał „nie podlega weryfikacji”, chociaż ta sprawa była w bazie: model uznawał twierdzenie za opinię i nie szukał.
- Gdy baza nie miała odpowiedzi, system nie pytał żadnego źródła na żywo.

## Dane (crawler)

| Zmiana | Dlaczego |
| --- | --- |
| **Google Fact Check:** pobieranie kolejnych stron wyników (`nextPageToken`) | Wcześniej crawler brał 100 weryfikacji na wydawcę. Teraz jest ich ponad 5000 (Demagog, OKO.press, AFP, FakeNews.pl). |
| **EUvsDisinfo przez MCP** (`mcp_tool`) | Ok. 19 900 spraw dezinformacji i 12 000 artykułów. Archiwum jest dociągane w kolejnych przebiegach. Parser powstał na podstawie prawdziwych odpowiedzi serwera. |
| **gov.pl:** 22 listy aktualności ministerstw i ok. 90 całych jednostek | Strona `mapa-strony` daje w jednym zapytaniu wszystkie podstrony jednostki. Dzięki temu da się pobrać cały gov.pl, a nie tylko pierwsze strony list. |
| **Scheduler:** źródła oznaczone `background` | Pobieranie archiwum gov.pl (kilka dni pracy) nie opóźnia alertów RCB, IMGW i RSO. |
| **Scheduler:** nowe źródła w kolejności od najkrótszego interwału | Przy starcie najpierw idą alerty i aktualności, a archiwa na końcu. |
| **`sources.d/*.yaml`** | Źródła w osobnych plikach zamiast jednego dużego `sources.yaml`. |
| **`scripts/crawler_run.sh`, `scripts/ingest_loop.sh`** | Crawler działa w tle, a nowe dane trafiają do bazy co 15 minut. |

## Weryfikacja (RAG)

| Zmiana | Dlaczego |
| --- | --- |
| **Znane narracje dezinformacyjne** | Twierdzenie podobne do opinii jest teraz sprawdzane w bazach fact-checków i EUvsDisinfo. Jeśli pasuje do opisanej dezinformacji, dostaje werdykt „fałszywe” lub „wprowadza w błąd” i flagę „Znana narracja dezinformacyjna”. Flagę ustawia kod, i tylko wtedy, gdy wśród cytatów jest ten, który obala twierdzenie. |
| **Wyszukiwanie wielojęzyczne** | Tekst angielski jest wyszukiwany także po angielsku, a odpowiedź zawsze jest po polsku. |
| **Wyszukiwanie na żywo** (`rag/live.py`) | Gdy baza nie rozstrzyga twierdzenia, system pyta Google Fact Check, wyszukiwarkę EUvsDisinfo i wyszukiwarkę gov.pl. Wyniki trafiają do bazy i dopiero stamtąd są cytowane, więc linki nadal pochodzą tylko z bazy. Zapytania idą przez `Fetcher` crawlera (robots.txt, odstępy). Wyłącza się to przez `PRAWDOWID_LIVE=0`. |
| **Interfejs** | Wyświetla wiersz „Znana narracja dezinformacyjna” i notatkę o wyszukiwaniu na żywo. |

## Wynik

| Przykład | Przed zmianami | Po zmianach |
| --- | --- | --- |
| Komunikat premiera o przestrzeni powietrznej | brak dowodów | **potwierdzone**, cytuje KPRM i MON |
| Tekst z EUvsDisinfo (po angielsku) | nie podlega weryfikacji | **fałszywe** i znana narracja, cytuje EUvsDisinfo |
| Twierdzenie spoza bazy (np. HPV dla osób w wieku 18–26 lat) | brak dowodów | **potwierdzone** na podstawie stron gov.pl pobranych na żywo |

## Ograniczenia

- Wyszukiwanie na żywo trwa 40–65 s, a interfejs obiecuje „do 30 sekund”.
- Trafność wyszukiwarki gov.pl przy ogólnych twierdzeniach jest słaba. Na przykład przy grypie dostajemy wyniki o COVID-19 i HPV, a system odpowiada wtedy „brak dowodów”.
- W User-Agencie wciąż jest tymczasowy mail `testtest@gmail.com`.
- Tekst interfejsu „tylko oficjalne komunikaty” jest nieaktualny, bo dochodzą fact-checkerzy i EUvsDisinfo.
