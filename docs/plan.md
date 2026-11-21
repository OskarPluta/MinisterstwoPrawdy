# PRAWDOWID implementation plan

Build a background importer that turns official notices, measurements, and verified fact-checks into evidence records. The chatbot searches those records for each claim, checks what the evidence says, and returns a short verdict in Polish with clickable citations.

Use the CFDA paper's claim decomposition, keyword and semantic search, and rank fusion. Start with one embedding model. Add a reranker if tests show that relevant evidence regularly reaches the shortlist too low. The prototype targets text and URL input through a responsive website and one additional channel or demo adapter. Both use the same verification service. [CFDA paper, §§3.1–3.3](https://trec.nist.gov/pubs/trec34/papers/cfdalab.rag.pdf)

## 1. Collect evidence in the background

Create a source catalog in `sources.yaml`. For each source, record its publisher, source type, approved host and paths, seed URL, adapter, refresh policy, and reuse terms. Record what places and topics the source covers. Use a Python worker with a one-off import command for setup and a scheduled job for refreshes.

| Source | Adapter | Unit of evidence |
|---|---|---|
| Gov.pl Infrastructure, RCB, MSWiA, KPRM, SOK, SAR, maritime offices, Wody Polskie | Discover links on selected news or notice listings and fetch individual pages. Wody Polskie starts with regional listings. Follow relevant attachments where permitted. | One article section, alert update, or PDF excerpt with its page number. [Gov.pl news example](https://www.gov.pl/web/infrastruktura/aktualnosci) · [Wody Polskie regional listings](https://www.gov.pl/web/wody-polskie/komunikaty-nawigacyjne) |
| ULC and UTK passenger rights, ministry contact pages, MSZ travel advisories | Import specific guides, FAQs, contact entries, or country profiles. Keep section headings and the country or region an advisory covers. | One self-contained section or advisory. [ULC guide](https://ulc.gov.pl/prawa-pasazera/poradnik-dla-pasazerow) · [UTK passenger pages](https://utk.gov.pl/pl/pasazerowie) · [Odyseusz advisories](https://odyseusz.gov.pl/strona-tematyczna/czym-sa-ostrzezenia-dla-podrozujacych) |
| CERT Polska | Use the RSS URL provided by the site's RSS picker to discover new notices. Fetch the linked pages for the full notice. Backfill from selected archive pages if needed. | One notice or section with its published date and identifier. [CERT communications](https://moje.cert.pl/komunikaty/) |
| IMGW | Use `/api/data/warningsmeteo` and `/api/data/warningshydro` on `danepubliczne.imgw.pl`. Add observations separately when needed. Confirm which Baltic products have a documented feed before adding them. | One warning per applicable area and validity window, or one observation with station, time, value, and unit. [IMGW API](https://danepubliczne.imgw.pl/pl/apiinfo) |
| GIOŚ | Use the current `/pjp-api/v1/rest/` endpoints. Cache station and sensor metadata, then fetch measurements for supported locations and pollutants. Read each endpoint's limits and units. | One observation keyed by sensor and measurement time. Store air-quality index data separately from pollutant readings. [GIOŚ API](https://powietrze.gios.gov.pl/pjp/content/api) |
| GDDKiA | Parse the public disruption table for the first integration, or use the documented KPD DATEX II feed when access is configured. | One restriction with road, section, direction when supplied, effect, and start and end times. [Disruption table](https://drogi.gddkia.gov.pl/informacje-drogowe/lista-utrudnien) · [KPD feed specification](https://kpd.gddkia.gov.pl/index.php/pl/specyfikacja-techniczna/) |
| Portal Pasażera and PAA radiation | Register for PKP PLK's API before promising live train checks. Import PAA reports for historical checks. Enable live radiation checks once a suitable documented feed and its coverage have been verified. | One dated train event or one cited report excerpt covering a stated period. [PKP PLK API](https://www.plk-sa.pl/klienci-i-kontrahenci/api-otwarte-dane) · [PAA quarterly reports](https://www.gov.pl/web/paa/komunikaty-kwartalne-prezesa) |
| Organizer's verified records and credible secondary sources | Import authorized JSON or CSV. Keep the original claim, finding, publication date, and links to the assessment and underlying evidence where available. | One verified incident or fact-check. Match it by claim and context. |

**Build the first three adapters in this order.**

1. **HTML.** Use `httpx` and `BeautifulSoup`. Fetch a listing on a configured host, follow known article links and pagination up to the configured limit, and extract the main body. Preserve headings, paragraphs, lists, and meaningful tables. Start with Gov.pl articles and keep saved fixtures for each page layout. Pull text from relevant PDFs with a PDF parser. Use OCR for scanned attachments as a later enhancement.
2. **RSS.** Use `feedparser` to read item IDs, publication dates, and article links. Pass each linked notice to the HTML extractor. Use the feed for discovery and the primary page for citations.
3. **Structured data.** Start with IMGW warnings. Validate the response against a typed schema. Map area, phenomenon, level, issuer, and time fields. Render a short Polish search passage directly from those fields. Keep the original values for verification.

The brief calls for **100 distinct documents or records, 10 domains, and at least two source types**. Count documents and upstream records before chunking. Track the publishing domain separately from the API host and avoid counting paths or mirrors as new domains. Start with the three adapters, then reuse them and import authorized secondary sources to meet the corpus requirement. Keep the shortlist of included sources auditable.

## 2. Store source evidence and search text together

Use SQLite with FTS5 and four application tables. One database and a NumPy similarity scan are sufficient starting points for the demo corpus. [SQLite FTS5](https://sqlite.org/fts5.html) · [Multilingual E5 model card](https://huggingface.co/intfloat/multilingual-e5-small)

| Table | Fields to start with |
|---|---|
| `sources` | `source_id`, publisher, publishing domain, source type, terms URL, public citation URL, refresh policy, last successful fetch, last error |
| `evidence` | `evidence_id`, source ID, upstream ID or canonical URL, version or content hash, title, citation URL, publication time, fetch time, observation time, validity start/end, area, status where supplied, structured fields |
| `passages` | `passage_id`, evidence ID and version, section heading, PDF page or anchor, exact excerpt where available, searchable text, embedding and model version |
| `checks` | Check ID, timestamp, masked input, per-claim verdicts and cited passage IDs, corpus/model versions, timing, and error or escalation status |

- **Articles and PDFs.** Split by section and then at paragraph boundaries. Start with about 150–300 words per passage and keep the heading and title in the search text. Keep a restriction, its exception, and its scope together. Check chunk lengths against the embedding model's token limit.
- **Warnings and readings.** Store facts in typed fields and build search text with a fixed template. A fictional example is `Ostrzeżenie IMGW: silny wiatr. Obszar: powiat X. Stopień: 2. Obowiązuje: 12:00–18:00.` Display it as a reading or warning from the source. Reserve quotation marks for source text reproduced verbatim.
- **Dates.** Store times in UTC and display them in the relevant local time zone. Track when an item was published, when the event happened or was valid, and when the system fetched it. Preserve unknown fields as `null`. A fresh fetch of an archived notice still describes the original event.
- **Updates.** Use the source ID and upstream ID or canonical URL as the stable identity. Use a content hash to identify changes. Store the updated evidence, FTS5 row, and precomputed vector together in a transaction. Keep superseded versions where reuse terms and retention allow, so past citations can be traced.
- **Collection failures.** Respect reuse terms, robots rules, and published API limits. Use request timeouts, bounded retries, and conditional requests where supported. A failed refresh keeps the previous snapshot and records its age. A missing item in a new snapshot is treated according to the feed's documented semantics. If a source is stale or fails mid-import, keep the last successful version and surface that limit to the verifier.

## 3. Search, verify, and answer

1. **Receive input.** Have the web UI and demo adapter call the same `POST /verify` endpoint. Extract the article text for a user-submitted URL with a bounded fetcher that validates public HTTP(S) destinations on every redirect and enforces time and size limits. The submitted page supplies claims. A separate source catalog governs which material can serve as vetted evidence. Treat all extracted text as untrusted data and give the verification model no browsing or execution tools.
2. **Extract claims and scope.** Ask the model for structured output with one claim per item, plus entities, geography, date or time range, and words like `wszystkie`. Resolve relative dates using the request time in `Europe/Warsaw`. Return a clarification status when the location or period is essential and ambiguous.
3. **Run the two searches for each claim.** Use BM25 over titles, headings, and text. In parallel, encode each short search query with `multilingual-e5-small` and compare it with the precomputed embeddings. Use the model's `query: ` and `passage: ` prefixes. Use explicit fields for known station IDs, regions, pollutants, and time windows. Retain relevant historical material when checking claims about the past. Include neutral search wording that can surface corrections or counterevidence.
4. **Merge and shortlist.** Start with up to 20 results from each search, deduplicate by passage ID, and combine them using Reciprocal Rank Fusion. Add `1 / (60 + rank)` for each list a passage appears in, with ranks starting at 1. Take about 6–8 passages per claim as a starting point. Keep material that covers different places, source types, or sides of a conflict. Treat syndicated copies as one source of confirmation.
5. **Verify the evidence.** Give the model the shortlisted excerpts, structured fields, and source metadata. Ask it to classify each useful passage as supporting, contradicting, or providing context, and return a per-claim verdict with passage IDs and a short explanation of evidence strength. Use code for exact comparisons of dates, numbers, units, and locations whenever possible. A high search rank measures relevance. Evidence strength depends on whether the material addresses the claim's scope, period, and facts.
6. **Render the answer.** Map verdicts to `potwierdzone`, `fałszywe`, `wprowadzające w błąd`, `brak wystarczających dowodów`, or `nie podlega weryfikacji`. Validate that each cited ID belongs to the claim's shortlist. Resolve links, metadata, and exact excerpts from SQLite, then display them next to the finding. Show conflicts and stale coverage in plain Polish. Use a separate status for outage, out-of-scope input, or needed clarification. Record the check and send high-risk or recurring unresolved cases to the designated reviewer when the notification is configured. Report a referral only after it succeeds.

**Example.** For *„Czy IMGW wydał dziś ostrzeżenie stopnia 2 dla całego Mazowsza?”*, extract the issuer, issue date, level, and claimed area. Search for the relevant warnings and compare their issue timestamps and listed areas with the claim. For a few counties, cite the record's actual coverage and explain the scope mismatch if the evidence establishes it. If the feed is unavailable or its coverage is incomplete, report the limitation separately and avoid drawing a province-wide conclusion.

## 4. Build and test in this order

| Order | Deliverable | Acceptance check |
|---|---|---|
| 1 | Source catalog, SQLite schema, and HTML/RSS/IMGW importers | Fetch real evidence and resolve each citation URL. A second import of the same data creates zero duplicates. An amended warning produces a new version. Report corpus totals before chunking. |
| 2 | BM25, embeddings, and RRF | Test known evidence IDs for keyword, paraphrase, date-sensitive, and geographic queries. Track how often relevant evidence is in the shortlist. |
| 3 | Verifier, citation rendering, and abstention | Test supported, false, misleading, unverifiable, and insufficient-evidence cases. Include an expired warning, conflicting sources, an unavailable feed, a page containing instructions aimed at the model, and an invented citation ID. |
| 4 | Website and second channel/adapter | Submit the same claim through both against the same corpus snapshot. Compare verdicts and evidence IDs and exercise the complete automated path. |
| 5 | Evaluation and reproducibility | Save a dated corpus manifest, model versions, a labelled test set, metric results, and setup instructions. Measure end-to-end time, model usage, and cost. Report cost for 100 test checks and estimate cost for 1,000. |

Compare **BM25 only** with **BM25 + embeddings + RRF**. If good evidence is present but buried, try a multilingual reranker on the fused shortlist and keep it if it improves verdicts or citations within the latency target. If good evidence is missing, check source coverage and query construction first. [Multilingual reranker candidate](https://huggingface.co/BAAI/bge-reranker-v2-m3)

Use the competition targets when reporting results: **macro-F1 ≥ 0.75** on the public set, **citation coverage ≥ 95%**, **citation correctness ≥ 85%**, **correct abstention ≥ 80%**, **prompt-injection resistance ≥ 90%**, and **median text/URL response ≤ 30 seconds**. Verify full end-to-end operation and cross-channel consistency. These are targets to measure during implementation.

Competition reference: `MSZ - Pełna treść zadania konkursowego(3).pdf`, §§2–7.
