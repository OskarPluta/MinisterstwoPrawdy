# intake: materiał od użytkownika → tekst do sprawdzenia

Pakiet `intake` zamienia to, co przysyła użytkownik (link do strony, film w serwisie YouTube, zdjęcie, zrzut ekranu, PDF), na obiekty `rag.attachments.Attachment`: tekst, datę materiału, notatki i ograniczenia. Werdyktu nie wydaje. Robi to `rag.verify.Verifier`, który dostaje załączniki obok tekstu.

**Materiał to przedmiot sprawdzenia, a nie dowód.** Model ekstrakcji widzi go jako escapowany blok `<attachment>`, oddzielny od `<user_input>`. Twierdzenia z materiału przechodzą to samo wyszukiwanie i ten sam werdykt co tekst. Nic z materiału nie jest cytowane ani linkowane, nic nie trafia do bazy dowodów.

```text
tekst + pliki ─► Intake.collect ─► linki z tekstu (maks. 2) ─► youtube.py | web.py ─► SafeFetcher
                               └─► pliki (maks. 3, typ po zawartości) ─► image.py | pdf.py ─► OCR
                                                       │
                                       list[Attachment] ─► Verifier.verify(text, now, attachments)
```

## Rodzaje materiału

| Wejście | Co odczytujemy | Czego nie robimy (mówimy to użytkownikowi) |
| --- | --- | --- |
| Link do strony | Tytuł, treść (`crawler.extract.extract_article`), data publikacji, przekierowanie, czy domena jest w katalogu źródeł | JavaScript, logowanie, strony z CAPTCHA/antybotem, strony zakazane w robots.txt |
| Link do PDF lub obrazu | Jak plik poniżej | |
| YouTube | Z kluczem `YOUTUBE_API_KEY`: tytuł, kanał, opis, data publikacji (Data API v3). Bez klucza: tytuł i kanał (oEmbed) | Nagranie i napisy (`captions.download` działa tylko dla właściciela filmu; skrypty pobierające transkrypcje łamią regulamin YouTube) |
| Zdjęcie, zrzut ekranu | EXIF (data, aparat, obecność GPS), tekst przez OCR, widoczne daty i autor, jednozdaniowy opis | Wykrywanie edycji, wyszukiwanie wcześniejszych wystąpień obrazu (odwrotne wyszukiwanie) |
| PDF | Warstwa tekstowa (pierwsze 30 stron), metadane (daty, program); skan: OCR pierwszych 3 stron | Pliki z hasłem, formularze, JavaScript w PDF |

Daty materiału (publikacja, EXIF, utworzenie pliku) trafiają do modelu i do notatki „materiał z dnia X może być wyrwany z kontekstu” przy twierdzeniach z materiału starszego niż 30 dni (scenariusz B). Notatka nigdy nie zmienia werdyktu.

## Pobieranie linków (`fetch.py`)

Te same zasady co crawler: User-Agent `FaktBot/<wersja> (+mail; pobranie na prośbę użytkownika)`, robots.txt przy każdym adresie i przekierowaniu (ten sam token `FaktBot`, więc strona blokująca crawler blokuje też to), bez proxy, stealth, logowania i JavaScriptu. Wyzwanie antybotowe albo 403/429/503 kończy próbę z komunikatem dla użytkownika (scenariusz J). Odstęp między pobraniami strony z jednego hosta to 3 s.

Dodatkowo, bo adres podaje obcy człowiek, a serwer widzi usługi wewnętrzne (SSRF):

- tylko `http`/`https`, bez loginu i hasła w adresie, tylko porty 80 i 443,
- każdy host jest rozwiązywany i **każdy** adres musi być publiczny (bez sieci prywatnych, loopback, link-local, CGNAT, metadanych chmury); to samo po każdym przekierowaniu i dla adresu faktycznie połączonego (ochrona przed DNS rebinding, w miarę możliwości transportu),
- maks. 3 przekierowania, 10 s, 5 MB treści,
- klucz API (YouTube) idzie w nagłówku, tylko do hosta z pierwotnego adresu.

Bez maila kontaktowego (`INTAKE_CONTACT` albo `CRAWLER_CONTACT`) linki nie są pobierane, a użytkownik dostaje o tym informację.

## OCR (`ocr.py`)

`PRAWDOWID_OCR`:

- `llm` (domyślnie): model wizyjny skonfigurowanego dostawcy (`rag.llm.transcribe_image`, ten sam klucz co weryfikacja). Obraz trafia do dostawcy poza EOG. Przed wysłaniem jest przeskalowany do 2000 px i zakodowany od nowa, co usuwa EXIF i GPS. Gdy jest zainstalowany Tesseract, służy jako zapas przy awarii.
- `tesseract`: lokalnie (`apt install tesseract-ocr tesseract-ocr-pol`). Bez kosztu i bez wysyłania obrazu, ale słabszy na zrzutach z mediów społecznościowych.
- `none`: bez OCR. Obrazy dostają ograniczenie „odczyt wyłączony”.

Koszt: jedno dodatkowe wywołanie modelu na obraz albo stronę skanu (tag `transcribe` w `Usage`).

## Prywatność i retencja

Pliki i pobrane strony są przetwarzane w pamięci i nie są zapisywane. Log `checks` zawiera hash SHA-256 pliku, typ, nazwę lub adres (maskowane) i do 500 znaków odczytanego tekstu (maskowane), bez obrazów. `meta.dhash` (64-bitowy hash percepcyjny) zostaje w pamięci jako podstawa do przyszłego grupowania podobnych zgłoszeń. Opis obrazu nie identyfikuje osób z twarzy.

## Polecenia i testy

```bash
uv run intake read plik.pdf zrzut.png https://example.com/artykul [--ocr none|tesseract|llm] [--json]
uv run pytest backend/intake      # lokalny serwer HTTP i MockTransport, bez sieci i LLM
```

Testy generują obrazy i PDF w locie (`tests/intake_helpers.py`), więc w repo nie ma plików binarnych.

## Znane ograniczenia

- Brak lokalnego indeksu obrazów z incydentów (pHash) i brak odwrotnego wyszukiwania obrazu; `dhash` jest liczony, ale z niczym nieporównywany.
- Brak transkrypcji audio i wideo (np. `faster-whisper`).
- Ochrona przed DNS rebinding sprawdza adres połączenia dopiero po wysłaniu żądania GET (bez treści).
- Strony renderowane JavaScriptem i serwisy za logowaniem (Facebook, X, TikTok) zwykle się nie odczytają; użytkownik dostaje prośbę o zrzut ekranu.
