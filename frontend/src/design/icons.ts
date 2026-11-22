// The 14 Lucide outline glyphs (ISC licence) from the Ministerstwo Prawdy design system,
// copied from components/bundle.js: 24px grid, drawn in currentColor with a 2px stroke.
// Added for the message composer (extra.tsx), same set and style: paperclip, x, arrow-right, loader.

export type IconName =
  | "house"
  | "search"
  | "user"
  | "chevron-right"
  | "info"
  | "circle-check"
  | "triangle-alert"
  | "circle-x"
  | "external-link"
  | "menu"
  | "file-text"
  | "calendar"
  | "log-in"
  | "download"
  | "paperclip"
  | "x"
  | "arrow-right"
  | "loader";

type Part = readonly ["path" | "circle" | "rect", Readonly<Record<string, string>>];

export const ICONS: Record<IconName, readonly Part[]> = {
  "house": [
    ["path", {"d": "M15 21v-8a1 1 0 0 0-1-1h-4a1 1 0 0 0-1 1v8"}],
    ["path", {"d": "M3 10a2 2 0 0 1 .709-1.528l7-6a2 2 0 0 1 2.582 0l7 6A2 2 0 0 1 21 10v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"}],
  ],
  "search": [
    ["path", {"d": "m21 21-4.34-4.34"}],
    ["circle", {"cx": "11", "cy": "11", "r": "8"}],
  ],
  "user": [
    ["path", {"d": "M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"}],
    ["circle", {"cx": "12", "cy": "7", "r": "4"}],
  ],
  "chevron-right": [
    ["path", {"d": "m9 18 6-6-6-6"}],
  ],
  "info": [
    ["circle", {"cx": "12", "cy": "12", "r": "10"}],
    ["path", {"d": "M12 16v-4"}],
    ["path", {"d": "M12 8h.01"}],
  ],
  "circle-check": [
    ["circle", {"cx": "12", "cy": "12", "r": "10"}],
    ["path", {"d": "m16 9-5.5 5.5L8 12"}],
  ],
  "triangle-alert": [
    ["path", {"d": "m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"}],
    ["path", {"d": "M12 9v4"}],
    ["path", {"d": "M12 17h.01"}],
  ],
  "circle-x": [
    ["circle", {"cx": "12", "cy": "12", "r": "10"}],
    ["path", {"d": "m15 9-6 6"}],
    ["path", {"d": "m9 9 6 6"}],
  ],
  "external-link": [
    ["path", {"d": "M15 3h6v6"}],
    ["path", {"d": "M10 14 21 3"}],
    ["path", {"d": "M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"}],
  ],
  "menu": [
    ["path", {"d": "M4 5h16"}],
    ["path", {"d": "M4 12h16"}],
    ["path", {"d": "M4 19h16"}],
  ],
  "file-text": [
    ["path", {"d": "M6 22a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h8a2.4 2.4 0 0 1 1.704.706l3.588 3.588A2.4 2.4 0 0 1 20 8v12a2 2 0 0 1-2 2z"}],
    ["path", {"d": "M14 2v5a1 1 0 0 0 1 1h5"}],
    ["path", {"d": "M10 9H8"}],
    ["path", {"d": "M16 13H8"}],
    ["path", {"d": "M16 17H8"}],
  ],
  "calendar": [
    ["path", {"d": "M8 2v3"}],
    ["path", {"d": "M16 2v3"}],
    ["rect", {"x": "3", "y": "3", "width": "18", "height": "18", "rx": "2"}],
    ["path", {"d": "M3 9h18"}],
  ],
  "log-in": [
    ["path", {"d": "m10 17 5-5-5-5"}],
    ["path", {"d": "M15 12H3"}],
    ["path", {"d": "M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4"}],
  ],
  "download": [
    ["path", {"d": "M12 15V3"}],
    ["path", {"d": "M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"}],
    ["path", {"d": "m7 10 5 5 5-5"}],
  ],
  "paperclip": [
    ["path", {"d": "m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 18 8.84l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48"}],
  ],
  "x": [
    ["path", {"d": "M18 6 6 18"}],
    ["path", {"d": "m6 6 12 12"}],
  ],
  "arrow-right": [
    ["path", {"d": "M5 12h14"}],
    ["path", {"d": "m12 5 7 7-7 7"}],
  ],
  "loader": [
    ["path", {"d": "M21 12a9 9 0 1 1-6.219-8.56"}],
  ],
};
