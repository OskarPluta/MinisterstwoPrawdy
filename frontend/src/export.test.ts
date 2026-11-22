import { describe, expect, test } from "bun:test";
import type { CheckResult } from "./api";
import { CSV_COLUMNS, exportFilename, resultAsCsv, resultAsJson, safeCell } from "./export";

const RES: CheckResult = {
  check_id: "0b7f3c8e-1d2a-4c5b-9e6f-7a8b9c0d1e2f",
  status: "ok",
  message: null,
  injection_detected: false,
  duration_ms: 1200,
  inputs: [],
  claims: [
    {
      claim: '=HYPERLINK("http://x.test","kliknij")',
      verdict: "false",
      verdict_pl: "fałszywe",
      explanation: 'Mówi "tak", potem\nnie.',
      citations: [
        { n: 1, passage_id: "P7", role: "contradicts", title: "Komunikat", publisher: "MEN", url: "https://gov.test/a",
          date_label: "21.11.2026", text: "Szkoły działają.", is_verbatim: true, attribution: null },
      ],
      notes: ["-uwaga"],
      downgraded_from: null,
      conflict: false,
      known_disinformation: true,
      strength: { level: "moderate", level_pl: "umiarkowana", reasons: ["Jedno źródło.", "Urzędowe."] },
      origin: "user",
    },
    {
      claim: "Drugie", verdict: "insufficient", verdict_pl: "brak wystarczających dowodów", explanation: "Brak.",
      citations: [], notes: [], downgraded_from: "true", conflict: false, known_disinformation: false,
      strength: { level: "none", level_pl: "brak", reasons: ["Brak źródeł."] },
      origin: "user",
    },
  ],
};
const CTX = { checkedAt: "2026-11-22T23:30:00Z", shared: true };

describe("client-side export", () => {
  test("JSON: versioned format, no check_id, no passage_id, Warsaw time", () => {
    const text = resultAsJson(RES, CTX, new Date("2026-11-23T08:00:00Z"));
    const doc = JSON.parse(text);
    expect(doc.format).toBe("prawdowid-export");
    expect(doc.version).toBe(1);
    expect(doc.exported_at).toBe("2026-11-23T08:00:00Z");
    expect(doc.checks[0].checked_at).toBe("2026-11-22T23:30:00Z");
    expect(doc.checks[0].checked_at_local).toBe("2026-11-23T00:30:00+01:00");
    expect(doc.checks[0].claims[0].claim).toBe(RES.claims[0]!.claim); // JSON keeps text as is
    expect(text).not.toContain(RES.check_id);
    expect(text).not.toContain("passage_id");
  });

  test("CSV: BOM, header, one row per claim, formulas neutralised, quoting", () => {
    const text = resultAsCsv(RES, CTX);
    expect(text.startsWith("﻿" + CSV_COLUMNS.join(",") + "\r\n")).toBe(true);
    expect(text).not.toContain(RES.check_id);
    expect(text).toContain(`"'=HYPERLINK(""http://x.test"",""kliknij"")"`);
    expect(text).toContain(",'-uwaga,");
    expect(text).toContain('"Mówi ""tak"", potem\nnie."');
    expect(text).toContain("2026-11-23 00:30:00,2026-11-22T23:30:00Z,ok,1,2,");
    expect(text).toContain(",2,2,Drugie,insufficient,");
  });

  test("a result without claims still gives one row", () => {
    const rows = resultAsCsv({ ...RES, claims: [], status: "out_of_scope", message: "@Brak" }, CTX).trim().split("\r\n");
    expect(rows.length).toBe(2);
    expect(rows[1]).toContain(",'@Brak,");
  });

  test("safeCell and filename", () => {
    for (const evil of ["=1", "+1", "-1", "@A", "\tx", "\rx", "＝1"]) expect(safeCell(evil)).toBe("'" + evil);
    expect(safeCell("a = b")).toBe("a = b");
    expect(safeCell(-3)).toBe("-3");
    expect(safeCell(null)).toBe("");
    expect(exportFilename("csv", new Date("2026-11-22T23:30:00Z"))).toBe("prawdowid-wynik-2026-11-23.csv");
  });
});
