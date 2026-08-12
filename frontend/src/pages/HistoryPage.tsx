import { useEffect, useState } from "react";
import { Link } from "react-router";

import { api } from "../api";
import { Badge, EmptyState, ErrorNote, Spinner } from "../components/Ui";
import { STATUS_LABELS, dateTime } from "../format";
import type { Analysis } from "../types";

function statusTone(status: string) {
  if (status === "failed") return "danger" as const;
  if (status === "compared") return "success" as const;
  if (status === "pending" || status === "extracting") return "accent" as const;
  return "neutral" as const;
}

export default function HistoryPage() {
  const [items, setItems] = useState<Analysis[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    const load = () =>
      api
        .history()
        .then((data) => {
          if (active) setItems(data);
        })
        .catch((err: Error) => {
          if (active) setError(err.message);
        });

    void load();
    // Análises rodam em fila; sem este poll a lista fica mentindo sobre o estado.
    const timer = window.setInterval(load, 5000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, []);

  return (
    <div className="mx-auto max-w-5xl space-y-4 p-6">
      <h1 className="text-lg font-semibold text-slate-900">
        Histórico de análises
      </h1>

      {error && <ErrorNote>{error}</ErrorNote>}
      {!items && !error && <Spinner label="Carregando…" />}

      {items?.length === 0 && (
        <EmptyState>
          Nenhuma análise ainda.{" "}
          <Link to="/nova" className="font-medium underline">
            Envie dois PDFs
          </Link>{" "}
          para começar.
        </EmptyState>
      )}

      {items && items.length > 0 && (
        <div className="overflow-hidden rounded-lg border border-slate-200 bg-white">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-4 py-2 font-medium">Título</th>
                <th className="px-4 py-2 font-medium">Situação</th>
                <th className="px-4 py-2 font-medium">Divergências</th>
                <th className="px-4 py-2 font-medium">Criada em</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {items.map((analysis) => (
                <tr key={analysis.id} className="hover:bg-slate-50">
                  <td className="px-4 py-2">
                    <Link
                      to={`/analises/${analysis.id}`}
                      className="font-medium text-slate-900 hover:underline"
                    >
                      {analysis.title || "(sem título)"}
                    </Link>
                    {analysis.error && (
                      <div className="text-xs text-red-600">
                        {analysis.error}
                      </div>
                    )}
                  </td>
                  <td className="px-4 py-2">
                    <Badge tone={statusTone(analysis.status)}>
                      {STATUS_LABELS[analysis.status] ?? analysis.status}
                    </Badge>
                  </td>
                  <td className="tabular px-4 py-2 text-slate-600">
                    {analysis.summary
                      ? `${analysis.summary.erros ?? 0} erro(s), ${analysis.summary.avisos ?? 0} aviso(s)`
                      : "—"}
                  </td>
                  <td className="px-4 py-2 text-slate-500">
                    {dateTime(analysis.created_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
