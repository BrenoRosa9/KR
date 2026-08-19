import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router";

import { api } from "../api";
import { DocumentPanel } from "../components/DocumentPanel";
import { FindingsList } from "../components/FindingsList";
import { PdfViewer, type Highlight } from "../components/PdfViewer";
import { ReviewTable } from "../components/ReviewTable";
import {
  Badge,
  Button,
  Card,
  ErrorNote,
  InfoNote,
  Spinner,
  Stat,
} from "../components/Ui";
import { STATUS_LABELS, dateTime, num } from "../format";
import type {
  AnalysisDetail,
  Finding,
  Observation,
} from "../types";

type Tab = "findings" | "review" | "documents";

const RUNNING = new Set(["pending", "extracting"]);

export default function AnalysisPage() {
  const { id = "" } = useParams();
  const editable = true;

  const [detail, setDetail] = useState<AnalysisDetail | null>(null);
  // A observação não carrega o papel do documento; ela é pedida por papel.
  const [observations, setObservations] = useState<
    Record<"A" | "B", Observation[]>
  >({ A: [], B: [] });
  const [tab, setTab] = useState<Tab>("findings");
  const [role, setRole] = useState<"A" | "B">("A");
  const [highlights, setHighlights] = useState<{
    A: Highlight | null;
    B: Highlight | null;
  }>({ A: null, B: null });
  const [selectedFinding, setSelectedFinding] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const data = await api.analysis(id);
      setDetail(data);
      setError(null);
      if (!RUNNING.has(data.analysis.status)) {
        const [a, b] = await Promise.all([
          api.observations(id, { role: "A" }),
          api.observations(id, { role: "B" }),
        ]);
        setObservations({ A: a, B: b });
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Falha ao carregar.");
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  // Enquanto a análise está na fila, o estado só muda no servidor.
  useEffect(() => {
    if (!detail || !RUNNING.has(detail.analysis.status)) return;
    const timer = window.setInterval(() => void load(), 3000);
    return () => window.clearInterval(timer);
  }, [detail, load]);

  const selectFinding = useCallback((finding: Finding, prefer: "A" | "B") => {
    setSelectedFinding(finding.id);
    const toHighlight = (provenance: typeof finding.provenance_a) =>
      provenance?.page
        ? {
            page: provenance.page,
            bbox: provenance.bbox,
            label: finding.subject,
          }
        : null;

    setHighlights({
      A: toHighlight(finding.provenance_a),
      B: toHighlight(finding.provenance_b),
    });

    // Se o lado clicado não tem origem localizável, mostra o outro.
    const target =
      prefer === "A" ? finding.provenance_a : finding.provenance_b;
    setRole(target?.page ? prefer : prefer === "A" ? "B" : "A");
  }, []);

  async function recompare() {
    setBusy(true);
    try {
      await api.recompare(id);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Falha ao recomparar.");
    } finally {
      setBusy(false);
    }
  }

  if (error && !detail) {
    return (
      <div className="p-6">
        <ErrorNote>{error}</ErrorNote>
      </div>
    );
  }

  if (!detail) {
    return (
      <div className="p-6">
        <Spinner label="Carregando análise…" />
      </div>
    );
  }

  const { analysis, documents, extractions, findings, match } = detail;
  const running = RUNNING.has(analysis.status);
  const summary = analysis.summary ?? {};
  const observationCount = observations.A.length + observations.B.length;

  const extractionByRole = (target: "A" | "B") =>
    extractions.find((item) => item.role === target);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex flex-wrap items-center gap-3 border-b border-slate-200 bg-white px-4 py-2">
        <div className="min-w-0">
          <h1 className="truncate text-sm font-semibold text-slate-900">
            {analysis.title || "(sem título)"}
          </h1>
          <p className="text-xs text-slate-500">
            Criada em {dateTime(analysis.created_at)}
            {analysis.compared_at &&
              ` · comparada em ${dateTime(analysis.compared_at)}`}
          </p>
        </div>

        <Badge tone={analysis.status === "failed" ? "danger" : "neutral"}>
          {STATUS_LABELS[analysis.status] ?? analysis.status}
        </Badge>

        <div className="ml-auto flex items-center gap-2">
          <Button
            variant="ghost"
            onClick={() => void recompare()}
            disabled={!editable || busy || running}
          >
            Recomparar
          </Button>
          <a
            href={api.reportHtmlUrl(id)}
            target="_blank"
            rel="noreferrer"
            className="rounded-md px-3 py-1.5 text-sm font-medium text-slate-700 ring-1 ring-inset ring-slate-300 hover:bg-slate-50"
          >
            Laudo
          </a>
          <a
            href={api.reportPdfUrl(id)}
            className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-700"
          >
            PDF
          </a>
        </div>
      </header>

      {analysis.error && (
        <div className="px-4 pt-3">
          <ErrorNote>{analysis.error}</ErrorNote>
        </div>
      )}

      {running && (
        <div className="px-4 pt-3">
          <InfoNote>
            <span className="flex items-center gap-2">
              <Spinner />
              Processando os PDFs. A página se atualiza sozinha quando terminar.
            </span>
          </InfoNote>
        </div>
      )}

      <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 p-3 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <div className="min-h-0 space-y-3 overflow-auto pr-1">
          <div className="grid grid-cols-4 gap-2">
            <Stat
              label="Erros"
              value={summary.erros ?? 0}
              tone={summary.erros ? "danger" : "success"}
            />
            <Stat
              label="Avisos"
              value={summary.avisos ?? 0}
              tone={summary.avisos ? "warning" : "neutral"}
            />
            <Stat label="Informativos" value={summary.informativos ?? 0} />
            <Stat
              label="Vértices pareados"
              value={match?.pairs ?? "—"}
            />
          </div>

          {match?.systematic && (
            <div className="rounded-md border border-indigo-200 bg-indigo-50 px-3 py-2 text-sm text-indigo-900">
              <div className="font-medium">Padrão sistemático detectado</div>
              <p className="text-[13px] leading-snug">
                {match.systematic.message}
              </p>
              <p className="mt-0.5 text-xs text-indigo-700">
                Resíduo após remover o padrão:{" "}
                {num(match.systematic.residual_rms, 3)} m. Corrigida a causa, a
                maior parte das divergências abaixo tende a desaparecer.
              </p>
            </div>
          )}

          <nav className="flex gap-1 border-b border-slate-200">
            {(
              [
                ["findings", `Divergências (${findings.length})`],
                ["review", `Revisão (${observationCount})`],
                ["documents", "Documentos e CRS"],
              ] as const
            ).map(([key, label]) => (
              <button
                key={key}
                onClick={() => setTab(key)}
                className={`-mb-px border-b-2 px-3 py-1.5 text-sm font-medium ${
                  tab === key
                    ? "border-slate-900 text-slate-900"
                    : "border-transparent text-slate-500 hover:text-slate-800"
                }`}
              >
                {label}
              </button>
            ))}
          </nav>

          {tab === "findings" && (
            <FindingsList
              findings={findings}
              selectedId={selectedFinding}
              onSelect={selectFinding}
            />
          )}

          {tab === "review" && (
            <ReviewTable
              observations={observations[role]}
              role={role}
              onRoleChange={setRole}
              onSelect={(observation) => {
                if (observation.page === null) return;
                setHighlights((current) => ({
                  ...current,
                  [role]: {
                    page: observation.page!,
                    bbox: observation.bbox,
                    label: observation.field,
                  },
                }));
              }}
              onSaved={() => void load()}
              editable={editable}
            />
          )}

          {tab === "documents" && (
            <div className="space-y-3">
              {(["A", "B"] as const).map((target) => (
                <DocumentPanel
                  key={target}
                  role={target}
                  document={documents[target]}
                  extraction={extractionByRole(target)}
                  recomputed={
                    target === "A" ? match?.recomputed_a : match?.recomputed_b
                  }
                  onSaved={() => void load()}
                  editable={editable}
                />
              ))}

              {match?.notes && match.notes.length > 0 && (
                <Card title="Notas do pareamento">
                  <ul className="list-inside list-disc space-y-0.5 text-[13px] text-slate-600">
                    {match.notes.map((note) => (
                      <li key={note}>{note}</li>
                    ))}
                  </ul>
                </Card>
              )}
            </div>
          )}
        </div>

        <div className="flex min-h-0 flex-col gap-2">
          <div className="flex rounded-md bg-slate-200 p-0.5">
            {(["A", "B"] as const).map((option) => (
              <button
                key={option}
                onClick={() => setRole(option)}
                className={`flex-1 truncate rounded px-3 py-1 text-xs font-medium ${
                  role === option
                    ? "bg-white text-slate-900 shadow-sm"
                    : "text-slate-600"
                }`}
              >
                {option} · {documents[option].filename}
              </button>
            ))}
          </div>

          <div className="min-h-0 flex-1">
            <PdfViewer
              key={documents[role].id}
              documentId={documents[role].id}
              highlight={highlights[role]}
              title={documents[role].filename}
            />
          </div>
        </div>
      </div>
    </div>
  );
}
