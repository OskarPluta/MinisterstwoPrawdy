// "Wersja kontrastowa": data-theme="contrast" on <html>, remembered per browser.
import { useCallback, useState } from "react";

const KEY = "mp-theme";

function read(): boolean {
  try {
    return window.localStorage.getItem(KEY) === "contrast";
  } catch {
    return false;
  }
}

function apply(contrast: boolean) {
  if (contrast) document.documentElement.dataset.theme = "contrast";
  else delete document.documentElement.dataset.theme;
}

/** Call before the first render so the page does not flash in the light theme. */
export function initTheme() {
  apply(read());
}

export function useContrast(): [boolean, () => void] {
  const [on, setOn] = useState(read);
  const toggle = useCallback(() => {
    setOn((prev) => {
      const next = !prev;
      apply(next);
      try {
        window.localStorage.setItem(KEY, next ? "contrast" : "light");
      } catch {
        /* private mode: the choice lasts for this page only */
      }
      return next;
    });
  }, []);
  return [on, toggle];
}
