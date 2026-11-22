// TEMPORARY: a strip with the admin panel link and its password, for local development only.
// The data comes from GET /dev/admin-link in server.ts, which answers only in dev mode, only to clients on this
// machine (loopback) and only when ADMIN_PASSWORD is in the environment (scripts/dev.sh exports the root .env).
// Otherwise the strip is not rendered. The password is never in the source or the bundle.
// To remove: delete this file, <DevAdminLink /> in App.tsx, the /dev/admin-link route in server.ts and
// `.app-devbar` in app.css.
import { useEffect, useState } from "react";

interface DevAdmin {
  port: number;
  password: string;
}

function isDevAdmin(d: unknown): d is DevAdmin {
  const o = d as Partial<DevAdmin> | null;
  return typeof o?.port === "number" && typeof o.password === "string" && o.password !== "";
}

export function DevAdminLink() {
  const [info, setInfo] = useState<DevAdmin | null>(null);

  useEffect(() => {
    fetch("/dev/admin-link", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((d: unknown) => setInfo(isDevAdmin(d) ? d : null))
      .catch(() => setInfo(null));
  }, []);

  if (!info) return null;
  // Same host the page was opened on (localhost or 127.0.0.1), the admin panel's port.
  const href = `${window.location.protocol}//${window.location.hostname}:${info.port}/`;
  return (
    <aside className="app-devbar" aria-label="Panel administracyjny (tymczasowo)">
      <div className="mp-container">
        <span className="app-devbar-tag">Tymczasowo, tylko lokalnie</span>
        <a className="mp-link" href={href} target="_blank" rel="noopener noreferrer">
          Panel administracyjny
        </a>
        <span>
          hasło: <code>{info.password}</code>
        </span>
      </div>
    </aside>
  );
}
