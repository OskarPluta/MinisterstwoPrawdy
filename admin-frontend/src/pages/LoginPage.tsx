import { useState, type FormEvent } from "react";
import { Button, Notice, TextField } from "../../../frontend/src/design/components";
import { api } from "../api";

export function LoginPage({ onLoggedIn }: { onLoggedIn: () => void }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.login(password);
      setPassword("");
      onLoggedIn();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="adm-login">
      <h1 className="app-h1">Zaloguj się do panelu</h1>
      <p className="app-lead">Panel jest dostępny tylko dla administratorów usługi.</p>
      {error && <Notice tone="danger">{error}</Notice>}
      <form onSubmit={submit} noValidate>
        <TextField
          label="Hasło administratora"
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
          autoFocus
        />
        <Button type="submit" disabled={busy || !password} icon="log-in">
          {busy ? "Logowanie…" : "Zaloguj się"}
        </Button>
      </form>
    </div>
  );
}
