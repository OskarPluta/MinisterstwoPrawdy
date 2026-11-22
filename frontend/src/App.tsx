import { useEffect } from "react";
import { PageMeta, SiteFooter, SiteHeader } from "./design/components";
import { CheckPage } from "./pages/CheckPage";
import { HowPage } from "./pages/HowPage";
import { SharedPage } from "./pages/SharedPage";
import { SourcesPage } from "./pages/SourcesPage";
import { useLinkInterception, usePath } from "./router";
import { useContrast } from "./theme";

const ROUTES: Record<string, { title: string; page: () => React.ReactElement }> = {
  "/": { title: "Sprawdź informację", page: CheckPage },
  "/zrodla": { title: "Źródła", page: SourcesPage },
  "/jak-to-dziala": { title: "Jak to działa", page: HowPage },
};

function NotFound() {
  return (
    <>
      <h1 className="app-h1">Nie znaleźliśmy tej strony</h1>
      <p className="app-lead">
        Sprawdź adres albo przejdź do{" "}
        <a className="mp-link" href="/">
          strony głównej
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
  // /wynik/<share_id>: read-only shared result (the id format is checked by the API).
  const shareId = /^\/wynik\/([A-Za-z0-9_-]{16})$/.exec(path)?.[1];
  const route = shareId ? { title: "Udostępniony wynik", page: NotFound } : ROUTES[path];
  const Page = route?.page ?? NotFound;

  useEffect(() => {
    document.title = `${route?.title ?? "Nie znaleziono"} · Copernicus`;
    const target = window.location.hash ? document.getElementById(window.location.hash.slice(1)) : null;
    if (target) target.scrollIntoView();
    else window.scrollTo(0, 0);
  }, [path, route]);

  return (
    <>
      <SiteHeader
        serviceName="Copernicus"
        tagline="Sprawdź informację · Ministerstwo Prawdy"
        searchHref={null}
        utilityLinks={[{ label: "Wersja kontrastowa", href: "#", onClick: toggleContrast, pressed: contrast }]}
        nav={[
          { label: "Sprawdź informację", href: "/", current: path === "/" },
          { label: "Źródła", href: "/zrodla", current: path === "/zrodla" },
          { label: "Jak to działa", href: "/jak-to-dziala", current: path === "/jak-to-dziala" },
        ]}
      />
      <main id="main" className="mp-container app-main" tabIndex={-1}>
        {shareId ? <SharedPage shareId={shareId} /> : <Page />}
        <div className="app-measure">
          <PageMeta updated="21.11.2026" publishedBy={["Departament Weryfikacji Informacji"]} />
        </div>
      </main>
      <SiteFooter
        name="Copernicus"
        columns={[
          {
            title: "Usługa",
            links: [
              { label: "Sprawdź informację", href: "/" },
              { label: "Źródła", href: "/zrodla" },
            ],
          },
          {
            title: "O serwisie",
            links: [
              { label: "Jak to działa", href: "/jak-to-dziala" },
              { label: "Co dzieje się z Twoją wiadomością", href: "/jak-to-dziala#prywatnosc" },
            ],
          },
          {
            title: "Pomoc",
            links: [
              { label: "Czego nie robimy", href: "/jak-to-dziala#granice" },
              { label: "Deklaracja dostępności", href: "/jak-to-dziala#dostepnosc" },
            ],
          },
        ]}
        note="© 2026 Ministerstwo Prawdy · projekt fikcyjny"
      />
    </>
  );
}
