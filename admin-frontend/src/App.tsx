import { useCallback, useEffect, useState } from "react";
import { SiteFooter, SiteHeader } from "../../frontend/src/design/components";
import { navigate, useLinkInterception, usePath } from "../../frontend/src/router";
import { useContrast } from "../../frontend/src/theme";
import { api, setUnauthorizedHandler, type AlertCounts } from "./api";
import { AlertsPage } from "./pages/AlertsPage";
import { AuditPage } from "./pages/AuditPage";
import { CostsPage } from "./pages/CostsPage";
import { DomainsPage } from "./pages/DomainsPage";
import { FailuresPage } from "./pages/FailuresPage";
import { HistoryPage } from "./pages/HistoryPage";
import { LoginPage } from "./pages/LoginPage";
import { OverviewPage } from "./pages/OverviewPage";
import { SourcesPage } from "./pages/SourcesPage";
import { Loading } from "./ui";

export interface PageProps {
  /** Re-read the open-alert counter in the header (after acknowledging alerts). */
  refreshAlerts: () => void;
}

const ROUTES: Record<string, { title: string; nav: string; page: (p: PageProps) => React.ReactElement }> = {
  "/": { title: "Pulpit", nav: "Pulpit", page: OverviewPage },
  "/historia": { title: "Historia wiadomości", nav: "Historia", page: HistoryPage },
  "/bledy": { title: "Nieudane sprawdzenia", nav: "Błędy", page: FailuresPage },
  "/koszty": { title: "Koszty i tokeny", nav: "Koszty", page: CostsPage },
  "/zrodla": { title: "Źródła", nav: "Źródła", page: SourcesPage },
  "/domeny": { title: "Domeny weryfikatorów", nav: "Domeny", page: DomainsPage },
  "/alerty": { title: "Alerty", nav: "Alerty", page: AlertsPage },
  "/dziennik": { title: "Dziennik zmian", nav: "Dziennik", page: AuditPage },
};
const ALERT_POLL_MS = 30_000;

function NotFound() {
  return (
    <>
      <h1 className="app-h1">Nie ma takiej strony panelu</h1>
      <p className="app-lead">
        Przejdź do{" "}
        <a className="mp-link" href="/">
          pulpitu
        </a>
        .
      </p>
    </>
  );
}

export function App() {
  useLinkInterception();
  const path = usePath();
  const [contrast, toggleContrast] = useContrast();
  const [auth, setAuth] = useState<"checking" | "anon" | "ok">("checking");
  const [counts, setCounts] = useState<AlertCounts | null>(null);
  const route = ROUTES[path];

  useEffect(() => {
    setUnauthorizedHandler(() => setAuth("anon"));
    api.me().then(
      () => setAuth("ok"),
      () => setAuth("anon"),
    );
  }, []);

  const refreshAlerts = useCallback(() => {
    api.alertCount().then(setCounts, () => {});
  }, []);

  useEffect(() => {
    if (auth !== "ok") return;
    refreshAlerts();
    const t = window.setInterval(refreshAlerts, ALERT_POLL_MS);
    return () => window.clearInterval(t);
  }, [auth, refreshAlerts]);

  useEffect(() => {
    const open = counts?.open ?? 0;
    document.title = `${open ? `(${open}) ` : ""}${auth === "ok" ? route?.title ?? "Nie znaleziono" : "Logowanie"} · Panel Copernicus`;
  }, [path, route, auth, counts]);

  useEffect(() => {
    window.scrollTo(0, 0); // block body: newer browsers return a Promise from scrollTo, not a cleanup function
  }, [path]);

  async function logout() {
    await api.logout().catch(() => {});
    setAuth("anon");
    navigate("/");
  }

  const nav =
    auth === "ok"
      ? Object.entries(ROUTES).map(([href, r]) => ({
          href,
          current: href === path,
          label: href === "/alerty" && counts?.open ? `${r.nav} (${counts.open})` : r.nav,
        }))
      : [];
  const Page = route?.page ?? NotFound;

  return (
    <>
      <SiteHeader
        serviceName="Copernicus"
        tagline="Panel administracyjny"
        searchHref={null}
        utilityLinks={[
          { label: "Wersja kontrastowa", href: "#", onClick: toggleContrast, pressed: contrast },
          ...(auth === "ok" ? [{ label: "Wyloguj", href: "#", onClick: () => void logout() }] : []),
        ]}
        nav={nav}
      />
      <main id="main" className="mp-container app-main adm-page" tabIndex={-1}>
        {auth === "checking" && <Loading label="Sprawdzanie sesji…" />}
        {auth === "anon" && <LoginPage onLoggedIn={() => setAuth("ok")} />}
        {auth === "ok" && <Page refreshAlerts={refreshAlerts} />}
      </main>
      <SiteFooter name="Copernicus · panel administracyjny" note="© 2026 Ministerstwo Prawdy · projekt fikcyjny · dostęp tylko dla administratorów" />
    </>
  );
}
