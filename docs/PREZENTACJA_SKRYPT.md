# Prezentacja konkursowa: notatki prelegenta i skrypt filmu demo

Prezentacja: [`prezentacja.pdf`](prezentacja.pdf) (10 slajdów). Liczby na slajdach pochodzą z [`RAPORT_TESTOW_I_KOSZTOW.md`](RAPORT_TESTOW_I_KOSZTOW.md) i z przebiegów w [`../eval_results/`](../eval_results/). Slajd 4 i lewa część slajdu 5 to zrzuty z działającej aplikacji (strona WWW + API, 22.11.2026, model openai/gpt-5.4-mini przez OpenRouter, wyszukiwanie na żywo włączone, `?now=2026-11-21T12:00:00+01:00`). Prawa część slajdu 5 to odpowiedzi z `eval_results/extended/20261122-073650_gpt-5.5/summary.md` (przypadki E-07, O-03, P-03), bez zmian w treści. Odpowiedzi modelu różnią się między przebiegami: z trzech przebiegów pytania o Nową Dębę na slajd trafił ten, który cytuje listopadowe ćwiczenie KOALICJA 26.2; jeden z pozostałych cytował wrześniowy pokaz MON.

> **Przed wysłaniem PDF: odśwież liczby korpusu.** Zrzut w repo ma dziś 98 rekordów z 7 domen, poniżej wymogu zadania (≥ 100 rekordów, ≥ 10 domen, ≥ 2 typy źródeł). Dlatego prezentacja nie podaje liczby rekordów (slajd 6 opisuje tylko korpus i źródła na żywo). Po crawlu i `rag ingest` zespołu uruchom `uv run rag stats` i dopisz prawdziwe liczby rekordów, domen i typów źródeł, np. na slajdzie 6. Zrób to tylko wtedy, gdy wymóg jest spełniony. Potem wyeksportuj PDF ponownie. Zrzut ekranu zakładki „Źródła” (licznik rekordów i domen) też rób dopiero po ingeście.

## Skrypt filmu demo (maks. 3:00)

Przygotowanie: baza z `data/snapshot` po `rag ingest`, klucz LLM w `.env`, `uv run api` i `bun run dev` w `frontend/`, bot Discord z `DISCORD_BOT_TOKEN`. Pytania o bieżące wydarzenia zadawaj z `?now=2026-11-21T12:00:00+01:00` (strona) i `DISCORD_NOW` (bot), bo zrzut ma dane z listopada 2026. Przed nagraniem sprawdź, że w kadrze nie ma klucza, tokenu ani danych osobowych. Nagrywaj prawdziwe odpowiedzi; jeśli system odpowie inaczej niż w scenariuszu, pokaż tę odpowiedź i dopasuj komentarz.

| Czas | Ekran | Lektor |
| --- | --- | --- |
| 0:00–0:15 | Okładka prezentacji | „Prawdowid: sprawdź pogłoskę tak łatwo, jak wysyłasz wiadomość. Każde ustalenie prowadzi do oficjalnego źródła, a gdy dowodów brakuje, system mówi to wprost.” |
| 0:15–0:30 | Telefon z wiadomością o manewrach w Nowej Dębie | „Takie wiadomości krążą po komunikatorach. Brzmią wiarygodnie, a zwykły obywatel nie wie, gdzie je sprawdzić.” |
| 0:30–1:10 | Strona „Copernicus”: wklejenie pytania A2-04, oczekiwanie, wynik | „Wklejam wiadomość. Po kilkunastu sekundach: fałszywe, siła dowodów silna. Przesłanki policzył kod: dwa niezależne źródła urzędowe, MON i Kancelaria Premiera. Każdy cytat ma tytuł, wydawcę, datę i link do gov.pl.” Najedź na przesłanki i kliknij jeden link. |
| 1:10–1:30 | Discord: `/sprawdz` z tym samym tekstem | „To samo pytanie na Discordzie. Ten sam silnik, ten sam werdykt i te same źródła. Nowy kanał to tylko adapter.” |
| 1:30–2:00 | Strona: pytanie o bakterię w wodzie w Myślenicach (H-02a) | „A teraz coś ważniejszego niż werdykt. Nie ma komunikatu dla Myślenic, więc system nie zgaduje: brak wystarczających dowodów, z wyjaśnieniem, co znalazł i dlaczego to nie wystarcza. Na żółto, nie na czerwono.” |
| 2:00–2:20 | Strona: pytanie zaczynające się od „SYSTEM: tryb administratora…” (E-07) | „Próba przejęcia systemu w treści pytania. Bot nie wydaje werdyktu, którego żąda atakujący.” |
| 2:20–2:35 | Zakładka „Źródła”, potem „Wersja kontrastowa” | „Każde źródło jest w jawnym katalogu, a strona ma wersję kontrastową dla osób słabiej widzących.” |
| 2:35–3:00 | Slajd 7 „Wyniki testów” | „Pokrycie cytowań 100%, zero zmyślonych źródeł, mediana poniżej 8 sekund, niecały grosz za sprawdzenie. Prawdowid: działający silnik, który można hostować w Polsce i który nigdy nie udaje, że wie.” |

## Notatki prelegenta (20–40 s na slajd)

### 1. Sprawdź pogłoskę tak łatwo, jak wysyłasz wiadomość.

Dzień dobry. Prawdowid to narzędzie, które pozwala obywatelowi sprawdzić pogłoskę tak łatwo, jak wysyła wiadomość. Wklejasz tekst z komunikatora, a po kilkunastu sekundach dostajesz po polsku werdykt, cytat z oficjalnego źródła i link. Nie budowaliśmy kolejnego chatbota. To system, w którym kod pilnuje, żeby każde ustalenie miało dowód, a gdy dowodu nie ma, system mówi to wprost. Działa od początku do końca w dwóch kanałach: na stronie WWW i na Discordzie.

### 2. Pogłoska jest szybsza niż sprostowanie

Problem nie polega tylko na tym, że fałszywe informacje istnieją. Pogłoska przychodzi kanałem, któremu ufamy, czyli od znajomej na Messengerze albo z grupy sąsiedzkiej, i dotyczy rzeczy, które budzą emocje: wody, wojska, świadczeń. Żeby ją sprawdzić, trzeba wiedzieć, który urząd ją ogłasza i czy komunikat jest aktualny. Większość z nas tego nie wie. A zwykły chatbot odpowie płynnie zawsze, także wtedy, gdy nie ma pojęcia. Dlatego zbudowaliśmy narzędzie, które mówi tylko tyle, ile da się udowodnić.

### 3. Wiadomość na wejściu, werdykt ze źródłem na wyjściu

Rozwiązanie w jednym obrazie. Wklejasz wiadomość na stronie albo piszesz do bota na Discordzie. Prawdowid wydziela z niej twierdzenia, szuka ich w oficjalnych źródłach, w razie potrzeby także na żywo, i porównuje. Na końcu kod sprawdza każdy cytat. Odpowiedź to jeden z pięciu werdyktów wymaganych w zadaniu, z siłą dowodów, cytatem i linkiem. Ważne: prośba o doprecyzowanie, pytanie spoza zakresu i błąd techniczny to osobne stany, a nie werdykty. Brak dowodów nigdy nie jest pokazywany na czerwono.

### 4. Narracja o wojsku obalona źródłami urzędowymi

To zrzut z działającej aplikacji, zrobiony na żywo, bez żadnej ręcznej poprawki. Obywatel pyta o filmik, według którego manewry w Nowej Dębie to wysyłka żołnierzy na front. To typowy przekaz z repertuaru manipulacji informacyjnej. Prawdowid odpowiada: fałsz, mocne dowody. Podstawa to dwa niezależne źródła urzędowe, czyli Ministerstwo Obrony Narodowej i Kancelaria Premiera, oba z ostatnich dni, i żadnego źródła sprzecznego. Każdy cytat prowadzi do strony gov.pl. Strona WWW i Discord korzystają z tego samego silnika, więc odpowiedź jest taka sama w obu kanałach.

### 5. Nie zgadujemy. Mówimy, czego nie wiemy

Drugie demo pokazuje coś, co naszym zdaniem jest ważniejsze od samego werdyktu: uczciwe wstrzymanie się. Pytanie o bakterię w wodzie w Myślenicach: system znalazł komunikat sanepidu, ale dla innej miejscowości, i mówi wprost, że to nie wystarcza. Zwróćcie uwagę, że ten wynik jest niebieski, a nie czerwony: brak dowodów to nie fałsz. Próba przejęcia systemu w treści pytania nie działa: bot nie wydaje werdyktu, którego żąda atakujący. Opinia dostaje ocenę „nie podlega weryfikacji”, a pytanie o lek jest poza zakresem i nie udaje werdyktu. Awaria API daje osobny komunikat techniczny.

### 6. Jeden rdzeń weryfikacji, wiele kanałów

Architektura ma jedną zasadę: kanały są cienkie, a cała logika siedzi w jednym rdzeniu. Strona WWW, bot na Discordzie, wiersz poleceń i API wołają ten sam obiekt Verifier. Nowy kanał, na przykład SMS albo WhatsApp, to tylko adapter. W rdzeniu są cztery kroki: model wydziela twierdzenia, wyszukiwanie hybrydowe łączy pełny tekst w Postgresie z wektorami multilingual-e5, model wydaje werdykt bez dostępu do narzędzi, a na końcu kod kontroluje cytaty i liczy siłę dowodów. Pod spodem jest korpus z crawlera, wyszukiwanie na żywo i wymienni dostawcy modeli, w tym polskie PCSS.

### 7. Twarde gwarancje dotrzymane w każdym przebiegu

Wyniki. Mierzyliśmy dokładnie to, co jury wymienia w sekcji szóstej. Pokrycie cytowań wynosi sto procent we wszystkich przebiegach. Zmyślonych źródeł było zero, bo pilnuje tego kod. Wstrzymanie się przy braku dowodów: od 83 do 100 procent. Próby ataku: odparte w najnowszych przebiegach. Mediana czasu: 7,9 sekundy przy celu 30. Spójność kanałów: 100 procent. Zbudowaliśmy też własny, trudny zestaw 49 pytań z wzorcowymi odpowiedziami. 63 procent odpowiedzi było w pełni poprawnych, a przy pozostałych system ani razu nie odwrócił werdyktu: najczęściej wybierał ostrożność, czyli „brak dowodów” albo prośbę o doprecyzowanie.

### 8. Prawie za darmo, bez jednego dostawcy

Koszt i skala. Przy modelu claude-haiku-5.5 sto testów kosztowało niecałe dziesięć centów, czyli mniej niż pół grosza za sprawdzenie. Policzyliśmy to ze zmierzonego zużycia tokenów. Serwer w Europejskim Obszarze Gospodarczym dla obu kanałów to około 45 do 90 złotych miesięcznie, a dla obywatela usługa jest darmowa. Wektory liczymy lokalnie. Nie jesteśmy przywiązani do jednego dostawcy: domyślnie używamy modeli hostowanych w polskim PCSS, w tym Bielika, a OpenAI czy Anthropic włącza jedna flaga. Nawet kampania na sto tysięcy zapytań to kilkaset złotych.

### 9. Sześć ryzyk z zadania i nasze zabezpieczenia

Sześć ryzyk, które wymienia zadanie, i nasze odpowiedzi. Halucynacje ogranicza kontrola cytatów w kodzie. Stronniczość źródeł: katalog jest jawny, a sama domena nigdy nie przesądza o werdykcie. Prywatność: log maskuje oczywiste dane osobowe. Prompt injection: źródła są dla modelu danymi, a nie poleceniami, i model nie ma żadnych narzędzi. Dostępność: prosty język i wersja kontrastowa. Nadużycia: limity rozmiaru zgłoszenia i grzeczny crawler.

### 10. Z prototypu do usługi MSZ

Co dalej. Część pracy jest już napisana i przetestowana na osobnych gałęziach, a teraz trafia do głównej: zdjęcia, zrzuty ekranu, PDF-y i linki na wejściu, licencje i retencja danych, limity żądań oraz eksport do CSV i JSON. Następny krok to sprawa moderatora dla nierozstrzygniętych zgłoszeń i podniesienie trafności na trudnym zestawie. Potem skala: SMS, WhatsApp i Messenger jako kolejne adaptery oraz integracja z OpenCTI i DISARM. MSZ dostaje działający, audytowalny silnik, który można hostować w Polsce i który nigdy nie udaje, że wie. Dziękujemy.

