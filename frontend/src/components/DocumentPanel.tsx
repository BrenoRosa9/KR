import { useState } from "react";

import { api } from "../api";
import { Badge, Button, Card, ErrorNote, InfoNote } from "./Ui";
import { bytes, num } from "../format";
import type { DocumentInfo, Extraction, RecomputedSummary } from "../types";

function CrsForm({
  extraction,
  onSaved,
  editable,
}: {
  extraction: Extraction;
  onSaved: () => void;
  editable: boolean;
}) {
  const [epsg, setEpsg] = useState(extraction.crs_epsg ?? "");
  const [datum, setDatum] = useState(extraction.datum_label ?? "SIRGAS2000");
  const [zone, setZone] = useState(
    extraction.utm_zone !== null ? String(extraction.utm_zone) : "",
  );
  const [hemisphere, setHemisphere] = useState(extraction.hemisphere ?? "S");
  const [ground, setGround] = useState(extraction.distances_are_ground);
  const [height, setHeight] = useState(String(extraction.average_height_m));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await api.updateCrs(extraction.id, {
        epsg,
        datum_label: datum,
        utm_zone: zone ? Number(zone) : null,
        hemisphere,
        distances_are_ground: ground,
        average_height_m: Number(height) || 0,
      });
      onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Falha ao salvar o CRS.");
    } finally {
      setBusy(false);
    }
  }

  const field =
    "w-full rounded border border-slate-300 px-2 py-1 text-sm outline-none focus:border-slate-500 disabled:bg-slate-50";

  return (
    <div className="space-y-2">
      {error && <ErrorNote>{error}</ErrorNote>}
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        <label className="space-y-0.5">
          <span className="text-xs text-slate-500">EPSG</span>
          <input
            value={epsg}
            onChange={(e) => setEpsg(e.target.value)}
            placeholder="31982"
            disabled={!editable}
            className={field}
          />
        </label>
        <label className="space-y-0.5">
          <span className="text-xs text-slate-500">Datum</span>
          <input
            value={datum}
            onChange={(e) => setDatum(e.target.value)}
            disabled={!editable}
            className={field}
          />
        </label>
        <label className="space-y-0.5">
          <span className="text-xs text-slate-500">Fuso</span>
          <input
            value={zone}
            onChange={(e) => setZone(e.target.value)}
            placeholder="22"
            disabled={!editable}
            className={field}
          />
        </label>
        <label className="space-y-0.5">
          <span className="text-xs text-slate-500">Hemisfério</span>
          <select
            value={hemisphere}
            onChange={(e) => setHemisphere(e.target.value)}
            disabled={!editable}
            className={field}
          >
            <option value="S">Sul</option>
            <option value="N">Norte</option>
          </select>
        </label>
        <label className="space-y-0.5">
          <span className="text-xs text-slate-500">Distâncias</span>
          <select
            value={ground ? "ground" : "grid"}
            onChange={(e) => setGround(e.target.value === "ground")}
            disabled={!editable}
            className={field}
          >
            <option value="ground">No terreno</option>
            <option value="grid">Na projeção</option>
          </select>
        </label>
        <label className="space-y-0.5">
          <span className="text-xs text-slate-500">Altitude média (m)</span>
          <input
            value={height}
            onChange={(e) => setHeight(e.target.value)}
            disabled={!editable}
            className={field}
          />
        </label>
      </div>
      <p className="text-xs leading-snug text-slate-500">
        O sistema de referência não é adivinhado: se o documento não o declara,
        confirme aqui. Datum errado desloca o polígono inteiro em dezenas de
        metros e transformaria a comparação em ruído.
      </p>
      {editable && (
        <Button variant="ghost" onClick={() => void save()} disabled={busy}>
          {busy ? "Salvando…" : "Salvar e recomparar"}
        </Button>
      )}
    </div>
  );
}

function Recomputed({ data }: { data: RecomputedSummary }) {
  return (
    <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
      <div>
        <dt className="text-slate-400">Área recalculada</dt>
        <dd className="tabular text-slate-800">{num(data.area_m2, 2)} m²</dd>
      </div>
      <div>
        <dt className="text-slate-400">Perímetro (terreno)</dt>
        <dd className="tabular text-slate-800">
          {num(data.perimeter_ground_m, 2)} m
        </dd>
      </div>
      <div>
        <dt className="text-slate-400">Fator de escala</dt>
        <dd className="tabular text-slate-800">{num(data.scale_factor, 7)}</dd>
      </div>
      <div>
        <dt className="text-slate-400">Fechamento</dt>
        <dd className="tabular text-slate-800">
          {data.closure_precision === null
            ? "—"
            : Number.isFinite(data.closure_precision)
              ? `1:${num(data.closure_precision, 0)}`
              : "exato"}
        </dd>
      </div>
    </dl>
  );
}

export function DocumentPanel({
  role,
  document,
  extraction,
  recomputed,
  onSaved,
  editable,
}: {
  role: "A" | "B";
  document: DocumentInfo;
  extraction: Extraction | undefined;
  recomputed: RecomputedSummary | null | undefined;
  onSaved: () => void;
  editable: boolean;
}) {
  return (
    <Card
      title={
        <span className="flex items-center gap-2">
          <Badge tone="accent">{role}</Badge>
          <span className="truncate" title={document.filename}>
            {document.filename}
          </span>
        </span>
      }
    >
      <div className="space-y-3">
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-500">
          <span>{document.page_count ?? "?"} página(s)</span>
          <span>{bytes(document.size_bytes)}</span>
          {document.producer && <span>Gerado por {document.producer}</span>}
          {document.triage?.needs_ocr && <Badge tone="danger">exigiu OCR</Badge>}
          {document.triage?.dominant_class && (
            <span>Tipo predominante: {document.triage.dominant_class}</span>
          )}
          {extraction?.table_strategy && (
            <span>Tabela via {extraction.table_strategy}</span>
          )}
        </div>

        {extraction ? (
          <>
            <CrsForm
              extraction={extraction}
              onSaved={onSaved}
              editable={editable}
            />

            {recomputed && <Recomputed data={recomputed} />}

            {extraction.errors && extraction.errors.length > 0 && (
              <ErrorNote>
                <ul className="list-inside list-disc space-y-0.5">
                  {extraction.errors.map((message) => (
                    <li key={message}>{message}</li>
                  ))}
                </ul>
              </ErrorNote>
            )}

            {extraction.warnings && extraction.warnings.length > 0 && (
              <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                <ul className="list-inside list-disc space-y-0.5">
                  {extraction.warnings.map((message) => (
                    <li key={message}>{message}</li>
                  ))}
                </ul>
              </div>
            )}

            {extraction.stages && (
              <details className="text-xs text-slate-500">
                <summary className="cursor-pointer select-none">
                  Estágios da extração
                </summary>
                <ul className="mt-1 space-y-0.5">
                  {extraction.stages.map((stage) => (
                    <li key={stage.stage}>
                      <span className={stage.ok ? "text-emerald-600" : "text-red-600"}>
                        {stage.ok ? "✓" : "✗"}
                      </span>{" "}
                      <span className="font-medium">{stage.stage}</span>
                      {stage.message && ` — ${stage.message}`}
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </>
        ) : (
          <InfoNote>Extração ainda não concluída para este documento.</InfoNote>
        )}
      </div>
    </Card>
  );
}
