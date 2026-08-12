import { useState, type FormEvent } from "react";

import { useAuth } from "../auth";
import { Button, ErrorNote } from "../components/Ui";

export default function LoginPage() {
  const { login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email, password);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Falha ao entrar.");
    } finally {
      setBusy(false);
    }
  }

  const inputClass =
    "w-full rounded-md border border-slate-300 px-3 py-2 text-sm outline-none focus:border-slate-500 focus:ring-1 focus:ring-slate-500";

  return (
    <div className="flex h-full items-center justify-center p-6">
      <form
        onSubmit={submit}
        className="w-full max-w-sm space-y-4 rounded-lg border border-slate-200 bg-white p-6 shadow-sm"
      >
        <div>
          <h1 className="text-lg font-semibold text-slate-900">
            Comparador de documentos técnicos
          </h1>
          <p className="mt-1 text-sm text-slate-500">
            Acesso restrito aos usuários cadastrados.
          </p>
        </div>

        {error && <ErrorNote>{error}</ErrorNote>}

        <label className="block space-y-1">
          <span className="text-sm font-medium text-slate-700">E-mail</span>
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            autoComplete="username"
            className={inputClass}
          />
        </label>

        <label className="block space-y-1">
          <span className="text-sm font-medium text-slate-700">Senha</span>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            autoComplete="current-password"
            className={inputClass}
          />
        </label>

        <Button type="submit" disabled={busy} className="w-full">
          {busy ? "Entrando…" : "Entrar"}
        </Button>
      </form>
    </div>
  );
}
