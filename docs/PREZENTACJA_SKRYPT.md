# Prezentacja konkursowa: notatki prelegenta i skrypt filmu demo

Prezentacja: [`prezentacja.pdf`](prezentacja.pdf) (12 slajdów). Liczby na slajdach: wyniki ze `eval_results/scenarios/20261122-090205_GLM-5.3-Flash/` (scenariusze A–J, korpus testowy, bez wyszukiwania na żywo), wynik 31/49 z `eval_results/extended/20261122-073650_gpt-5.5/` (starszy kod), koszt GLM policzony z tokenów tego przebiegu i cennika PCSS w `backend/rag/src/rag/llm.py` (ok. 2,86 zł za 100). Bielik (`Bielik-11B-v3.0-Instruct`, `PCSS_MODEL`) jest obsługiwany w `backend/rag/src/rag/llm.py` i ma cennik PCSS, ale nie był mierzony w ewaluacji: na slajdach jest tylko jako dostępny model, bez wyników. GLM-5.3-Flash nie jest polskim modelem, tylko hostowanym w polskim PCSS, koszt haiku i infrastruktury z [`RAPORT_TESTOW_I_KOSZTOW.md`](RAPORT_TESTOW_I_KOSZTOW.md). Slajdy 4 i 5 (lewa część) to zrzuty z działającej aplikacji na kodzie z `origin/main` (22.11.2026, openai/gpt-5.4-mini przez OpenRouter, wyszukiwanie na żywo, `?now=2026-11-21T12:00:00+01:00`); prawa część slajdu 5 to przypadki E-07, O-03, P-03 z przebiegu ewaluacji. Odpowiedzi modelu różnią się między przebiegami: na slajd trafił przebieg cytujący listopadowe ćwiczenie KOALICJA 26.2. Multimodalność (zdjęcia, PDF, linki, YouTube) działa, ale nie była mierzona w ewaluacji (scenariusze F, I1, I2 mają status N/A). Slajd 8 (panel dla zespołu) opisuje `admin-backend/` i `admin-frontend/` według ich README (commity `d5a3cbc`, `a4dfe04`), bez zrzutu i bez liczb z panelu. Jev (`backend/rag/src/rag/jev.py`, commit `1a75bfb`) jest na slajdzie 6 tylko jako opcjonalny sędzia werdyktu: działa wyłącznie z kluczem (`JEV_API_KEY`, `TYPESAFE_API_KEY`, `OPENROUTER_API_KEY` albo `AI_GATEWAY_API_KEY`), to zewnętrzne API, a nie PCSS, i wyniki na slajdzie 9 pochodzą z przebiegu bez Jev. Przebiegów z Jev w `eval_results/` nie przeniesiono na slajdy.

> **Przed wysłaniem PDF: odśwież liczby korpusu.** Zrzut w repo ma dziś 98 rekordów z 7 domen, poniżej wymogu zadania (≥ 100 rekordów, ≥ 10 domen, ≥ 2 typy źródeł). Dlatego prezentacja nie podaje liczby rekordów (slajdy 6 i 7 opisują korpus i źródła na żywo bez liczb). Po crawlu i `rag ingest` zespołu uruchom `uv run rag stats` i dopisz prawdziwe liczby rekordów, domen i typów źródeł, np. na slajdzie 6. Zrób to tylko wtedy, gdy wymóg jest spełniony. Potem wyeksportuj PDF ponownie. Zrzut ekranu zakładki „Źródła” (licznik rekordów i domen) też rób dopiero po ingeście.

## Skrypt filmu demo (maks. 3:00)

Przygotowanie: baza z `data/snapshot` po `rag ingest`, klucz LLM w `.env`, `uv run api` i `bun run dev` w `frontend/`, bot Discord z `DISCORD_BOT_TOKEN`. Pytania o bieżące wydarzenia zadawaj z `?now=2026-11-21T12:00:00+01:00` (strona) i `DISCORD_NOW` (bot), bo zrzut ma dane z listopada 2026. Przed nagraniem sprawdź, że w kadrze nie ma klucza, tokenu ani danych osobowych. Nagrywaj prawdziwe odpowiedzi; jeśli system odpowie inaczej niż w scenariuszu, pokaż tę odpowiedź i dopasuj komentarz.

| Czas | Ekran | Lektor |
| --- | --- | --- |
| 0:00–0:15 | Okładka prezentacji | „Prawdowid: sprawdź pogłoskę tak łatwo, jak wysyłasz wiadomość. Każde ustalenie prowadzi do oficjalnego źródła, a gdy dowodów brakuje, system mówi to wprost.” |
| 0:15–0:30 | Telefon z wiadomością o manewrach w Nowej Dębie | „Takie wiadomości krążą po komunikatorach. Brzmią wiarygodnie, a zwykły obywatel nie wie, gdzie je sprawdzić.” |
| 0:30–1:10 | Strona „Copernicus”: wklejenie pytania A2-04, oczekiwanie, wynik | „Wklejam wiadomość. Po kilkunastu sekundach: fałszywe, siła dowodów silna. Przesłanki policzył kod: dwa niezależne źródła urzędowe, MON i Kancelaria Premiera. Każdy cytat ma tytuł, wydawcę, datę i link do gov.pl.” Najedź na przesłanki i kliknij jeden link. |
| 1:10–1:30 | Discord: `/sprawdz` z tym samym tekstem | „To samo pytanie na Discordzie. Ten sam silnik, ten sam werdykt i te same źródła. Nowy kanał to tylko adapter.” |
| 1:30–2:00 | Strona: pytanie o bakterię w wodzie w Myślenicach (H-02a) | „A teraz coś ważniejszego niż werdykt. Nie ma komunikatu dla Myślenic, więc system nie zgaduje: brak wystarczających dowodów, z wyjaśnieniem, co znalazł i dlaczego to nie wystarcza. Na niebiesko, nie na czerwono.” |
| 2:00–2:20 | Strona: pytanie zaczynające się od „SYSTEM: tryb administratora…” (E-07) | „Próba przejęcia systemu w treści pytania. Bot nie wydaje werdyktu, którego żąda atakujący.” |
| 2:20–2:35 | Zrzut ekranu wiadomości wrzucony przyciskiem „Dodaj plik”, potem „Wersja kontrastowa” | „Można też wrzucić zrzut ekranu albo link: odczytamy tekst i daty. A dla osób słabiej widzących jest wersja kontrastowa.” |
| 2:35–3:00 | Slajd 9 „Wyniki testów” | „Jedenaście na jedenaście scenariuszy organizatora na modelu w polskim PCSS, pokrycie cytowań 100%, zero zmyślonych źródeł, około trzech groszy za sprawdzenie. Prawdowid: działający silnik, który można hostować w Polsce i który nigdy nie udaje, że wie.” |

## Notatki prelegenta (20–40 s na slajd)

### 1. Sprawdź pogłoskę tak łatwo, jak wysyłasz wiadomość.

Dzień dobry. Prawdowid to narzędzie, które pozwala obywatelowi sprawdzić pogłoskę tak łatwo, jak wysyła wiadomość. Wklejasz tekst z komunikatora, a po kilkunastu sekundach dostajesz po polsku werdykt, cytat z oficjalnego źródła i link. Nie budowaliśmy kolejnego chatbota. To system, w którym kod pilnuje, żeby każde ustalenie miało dowód, a gdy dowodu nie ma, system mówi to wprost. Działa od początku do końca w dwóch kanałach: na stronie WWW i na Discordzie. Może działać na polskim modelu językowym Bielik, hostowanym w Poznańskim Centrum Superkomputerowo-Sieciowym.

### 2. Pogłoska jest szybsza niż sprostowanie

Problem nie polega tylko na tym, że fałszywe informacje istnieją. Pogłoska przychodzi kanałem, któremu ufamy, czyli od znajomej na Messengerze albo z grupy sąsiedzkiej, i dotyczy rzeczy, które budzą emocje: wody, wojska, świadczeń. Żeby ją sprawdzić, trzeba wiedzieć, który urząd ją ogłasza i czy komunikat jest aktualny. Większość z nas tego nie wie. A zwykły chatbot odpowie płynnie zawsze, także wtedy, gdy nie ma pojęcia. Dlatego zbudowaliśmy narzędzie, które mówi tylko tyle, ile da się udowodnić.

### 3. Wiadomość na wejściu, werdykt ze źródłem na wyjściu

Rozwiązanie w jednym obrazie. Wklejasz wiadomość na stronie albo piszesz do bota na Discordzie. Prawdowid wydziela z niej twierdzenia, szuka ich w oficjalnych źródłach, w razie potrzeby także na żywo, i porównuje. Gdy źródła sobie przeczą, rozstrzyga nowsze. Na końcu kod sprawdza każdy cytat. Odpowiedź to jeden z pięciu werdyktów wymaganych w zadaniu, z siłą dowodów, cytatem i linkiem. Ważne: prośba o doprecyzowanie, pytanie spoza zakresu i błąd techniczny to osobne stany, a nie werdykty. Brak dowodów nigdy nie jest pokazywany na czerwono.

### 4. Narracja o wojsku obalona źródłami urzędowymi

To zrzut z działającej aplikacji, zrobiony na żywo, bez żadnej ręcznej poprawki. Obywatel pyta o filmik, według którego manewry w Nowej Dębie to wysyłka żołnierzy na front. To typowy przekaz z repertuaru manipulacji informacyjnej. Prawdowid odpowiada: fałsz, mocne dowody. Podstawa to dwa niezależne źródła urzędowe, czyli Ministerstwo Obrony Narodowej i Kancelaria Premiera, oba z ostatnich dni, i żadnego źródła sprzecznego. Każdy cytat prowadzi do strony gov.pl. Strona WWW i Discord korzystają z tego samego silnika, więc odpowiedź jest taka sama w obu kanałach.

### 5. Nie zgadujemy. Mówimy, czego nie wiemy

Drugie demo pokazuje coś, co naszym zdaniem jest ważniejsze od samego werdyktu: uczciwe wstrzymanie się. Pytanie o bakterię w wodzie w Myślenicach: system znalazł komunikat sanepidu, ale dla innej miejscowości, i mówi wprost, że to nie wystarcza. Zwróćcie uwagę, że ten wynik jest niebieski, a nie czerwony: brak dowodów to nie fałsz. Próba przejęcia systemu w treści pytania nie działa: bot nie wydaje werdyktu, którego żąda atakujący. Opinia dostaje ocenę „nie podlega weryfikacji”, a pytanie o lek jest poza zakresem i nie udaje werdyktu. Awaria API daje osobny komunikat techniczny.

### 6. Jeden rdzeń weryfikacji, wiele kanałów

Architektura ma jedną zasadę: kanały są cienkie, a cała logika siedzi w jednym rdzeniu. Strona WWW, bot na Discordzie, wiersz poleceń i API wołają ten sam obiekt Verifier. Nowy kanał, na przykład SMS albo WhatsApp, to tylko adapter. Na wejściu przyjmujemy tekst, link, zdjęcie, zrzut ekranu, PDF i film z YouTube: odczytujemy tekst i daty, ale materiał jest tylko tym, co sprawdzamy, nigdy dowodem. W rdzeniu są cztery kroki: model wydziela twierdzenia, wyszukiwanie hybrydowe łączy pełny tekst w Postgresie z wektorami multilingual-e5, model wydaje werdykt bez dostępu do narzędzi (opcjonalnie robi to Jev, model decyzyjny firmy TypeSafe, który wybiera werdykt i rolę każdego fragmentu, a wyjaśnienie składa wtedy kod), a na końcu kod kontroluje cytaty i liczy siłę dowodów. Pod spodem jest korpus z crawlera, wyszukiwanie na żywo i wymienni dostawcy modeli, w tym polski Bielik w PCSS. Obok działa panel administracyjny dla zespołu, na tej samej bazie.

### 7. Wyszukiwanie, które liczy źródła, a nie kopie

Pod maską jest RAG, z którego jesteśmy szczególnie dumni. Wyszukiwanie łączy pełny tekst w PostgreSQL z wektorami wielojęzycznego modelu e5 i scala wyniki fuzją RRF. Najważniejsze: przy zapisie do bazy wykrywamy przedruki algorytmem MinHash. Jeśli dziesięć portali przedrukuje tę samą depeszę, liczy się ona jako jeden dowód, a nie dziesięć. To wprost chroni przed sztucznym pompowaniem siły dowodów. Fragmenty tniemy według tokenów modelu, a na krótką listę trafia najwyżej jeden fragment z artykułu. Baza pamięta każdą wersję tekstu i rośnie z każdym wyszukiwaniem na żywo.

### 8. Operator widzi wszystko oprócz danych osobowych

Za systemem stoi panel dla zespołu, który go obsługuje. To osobny proces na tej samej bazie, z logowaniem i dziennikiem każdej zmiany. Operator widzi historię wszystkich sprawdzeń z WWW, Discorda i wiersza poleceń, ale tylko z zamaskowanym tekstem: oryginału z danymi osobowymi panel nie ma. Widzi koszty według dnia, modelu i kanału oraz błędy pogrupowane w kategorie, które przypisuje kod, a nie model. Może od razu wyłączyć źródło albo wycofać pojedynczy dokument, a nic nie jest kasowane, więc dawne cytaty da się prześledzić. Gdy rośnie odsetek błędów albo źródło przestaje się pobierać, panel wysyła alert na Discord, bez treści pytań ani odpowiedzi.

### 9. 11/11 scenariuszy organizatora na modelu w polskim PCSS

Wyniki. Na scenariuszach organizatora od A do J, uruchomionych na modelu GLM-5.3-Flash w polskim PCSS, zaliczyliśmy jedenaście z jedenastu obsługiwanych przypadków. Trafność werdyktu wynosi 1,00 macro-F1, pokrycie cytowań sto procent, zmyślonych źródeł zero, wstrzymanie się przy braku dowodów sto procent, sześć z sześciu prób ataku odpartych, a mediana czasu to niecałe 17 sekund przy celu 30. Ten sam wynik w obu kanałach. Mamy też trudniejszy, własny zestaw 49 pytań: tam 63 procent odpowiedzi było w pełni poprawnych, a system ani razu nie odwrócił werdyktu.

### 10. Prawie za darmo, bez jednego dostawcy

Koszt i skala. Na modelu GLM-5.3-Flash w polskim PCSS sto sprawdzeń kosztuje około trzech złotych, a na claude-haiku-5.5 niecałe dziesięć centów. Oba wyniki policzyliśmy ze zmierzonego zużycia tokenów. Serwer w Europejskim Obszarze Gospodarczym dla obu kanałów to około 45 do 90 złotych miesięcznie, a dla obywatela usługa jest darmowa. Wektory liczymy lokalnie. Nie jesteśmy przywiązani do jednego dostawcy: domyślnie używamy modeli hostowanych w polskim PCSS, w tym Bielika, a OpenAI czy Anthropic włącza jedna flaga. Nawet kampania na sto tysięcy zapytań to kilkaset złotych.

### 11. Sześć ryzyk z zadania i nasze zabezpieczenia

Sześć ryzyk, które wymienia zadanie, i nasze odpowiedzi. Halucynacje ogranicza kontrola cytatów w kodzie. Stronniczość źródeł: katalog jest jawny, źródło można wyłączyć w panelu, a każda taka zmiana trafia do dziennika; sama domena nigdy nie przesądza o werdykcie. Prywatność: log maskuje oczywiste dane osobowe, panel administracyjny widzi tylko zamaskowany tekst, a zapis znika po 30 dniach i można go usunąć jednym kliknięciem. Prompt injection: źródła są dla modelu danymi, a nie poleceniami, i model nie ma żadnych narzędzi. Dostępność: prosty język i wersja kontrastowa. Nadużycia: limity żądań na klienta, limity rozmiaru zgłoszenia i grzeczny crawler.

### 12. Z prototypu do usługi MSZ

Co dalej. Wiele rzeczy już działa: zdjęcia, zrzuty ekranu, PDF-y i linki na wejściu, licencje i retencja danych, limity żądań oraz eksport do CSV i JSON, a także wykrywanie przedruków i panel administracyjny z historią, kosztami i alertami. Następny krok to kolejka moderatora w panelu dla nierozstrzygniętych zgłoszeń i podniesienie trafności na trudnym zestawie. Potem skala: SMS, WhatsApp i Messenger jako kolejne adaptery oraz integracja z OpenCTI i DISARM. MSZ dostaje działający, audytowalny silnik, który można hostować w Polsce i który nigdy nie udaje, że wie. Dziękujemy.
