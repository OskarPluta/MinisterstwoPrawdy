// Minimal client-side routing on the History API (server.ts serves index.html for every path).
// Same-origin <a href="/..."> clicks are intercepted globally, so design-system links need no changes.
import { useEffect, useSyncExternalStore } from "react";

const EVENT = "mp:navigate";

function subscribe(cb: () => void) {
  window.addEventListener("popstate", cb);
  window.addEventListener(EVENT, cb);
  return () => {
    window.removeEventListener("popstate", cb);
    window.removeEventListener(EVENT, cb);
  };
}

export function navigate(path: string) {
  if (path === window.location.pathname + window.location.hash) return;
  window.history.pushState(null, "", path);
  window.dispatchEvent(new Event(EVENT));
}

export function usePath(): string {
  return useSyncExternalStore(subscribe, () => window.location.pathname);
}

/** Intercepts plain left clicks on same-origin links so they navigate without a page load. */
export function useLinkInterception() {
  useEffect(() => {
    function onClick(e: MouseEvent) {
      if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      const a = (e.target as Element | null)?.closest?.("a");
      if (!a || a.target || a.hasAttribute("download")) return;
      const url = new URL(a.href, window.location.href);
      if (url.origin !== window.location.origin || url.pathname.startsWith("/api/")) return;
      // In-page anchors (skip link, #section) keep their native behaviour.
      if (url.pathname === window.location.pathname && url.hash) return;
      e.preventDefault();
      navigate(url.pathname + url.hash);
    }
    document.addEventListener("click", onClick);
    return () => document.removeEventListener("click", onClick);
  }, []);
}
