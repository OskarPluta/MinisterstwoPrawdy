# Zrzut crawla (`data/snapshot/`)

Zamrożony przebieg crawlera FaktBot, domyślne wejście `uv run rag ingest`. Nie nadpisuj go przypadkiem przebiegiem crawlera.

Treści należą do wydawców. Podajemy je z oznaczeniem źródła (pole `institution` i `url` w każdym rekordzie):

- **Portal gov.pl** (`www.gov.pl`): teksty na licencji [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/deed.pl) ([prawa autorskie gov.pl](https://www.gov.pl/web/gov/prawa-autorskie)). Materiały urzędowe są wolne od prawa autorskiego.
- **IMGW-PIB** (`danepubliczne.imgw.pl`): Źródłem pochodzenia danych jest Instytut Meteorologii i Gospodarki Wodnej – Państwowy Instytut Badawczy. Warunki: [Regulamin udostępniania danych](https://danepubliczne.imgw.pl/) (użycie niezarobkowe).
- **Policja, Urząd Morski w Szczecinie i Gdyni**: materiały urzędowe (art. 4 pkt 2 ustawy o prawie autorskim).
- **Straż Ochrony Kolei** (`kgsok.pl`): licencja nieokreślona przez wydawcę. To otwarta decyzja, opisana w [`docs/DANE_I_LICENCJE.md`](../../docs/DANE_I_LICENCJE.md#otwarte-ryzyka-decyzje-zespołu).

Usuwanie dokumentu ze zrzutu: wpis w `backend/crawler/removed.yaml`, potem `uv run crawler --data-dir data/snapshot forget` i commit. Pełna procedura: [`docs/DANE_I_LICENCJE.md`](../../docs/DANE_I_LICENCJE.md#procedura-usunięcia).
