import { useMemo, useState } from "react";

import { api } from "../api";
import { Badge, Button, EmptyState, ErrorNote, InfoNote } from "./Ui";
import { FIELD_LABELS, num } from "../format";
import type { Observation } from "../types";

const LOW_CONFIDENCE = 0.75;

function subjectOf(observation: Observation): string {
  if (observation.vertex_index !== null) return `V${observation.vertex_index + 1}`;
  if (observation.segment_index !== null)
    return `Lado ${observation.segment_index + 1}`;
  return "Documento";
}

function displayValue(observation: Observation): string {
  if (observation.value_text !== null) return observation.value_text;
  if (observation.value_num === null) return "";
  // Casas decimais suficientes para não esconder o que a comparação enxerga.
  return num(observation.value_num, observation.field === "area" ? 4 : 6);
}

function EditableCell({
  observation,
  onSaved,
  editable,
}: {
  observation: Observation;
  onSaved: () => void;
  editable: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isText = observation.value_text !== null;

  async function save() {
    setBusy(true);
    setError(null);
    try {
      if (isText) {
        await api.updateObservation(observation.id, { value_text: draft });
      } else {
        const parsed = Number(draft.replace(/\./g, "").replace(",", "."));
        if (!Number.isFinite(parsed)) {
          throw new Error("Valor numérico inválido.");
        }
        await api.updateObservation(observation.id, { value_num: parsed });
      }
      setEditing(false);
      onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Falha ao salvar.");
    } finally {
      setBusy(false);
    }
  }

  if (!editing) {
    return (
      <div className="flex items-center gap-1.5">
        <span className="tabular text-slate-900">
          {displayValue(observation) || "—"}
        </span>
        {observation.edited && <Badge tone="accent">corrigido</Badge>}
        {editable && (
          <button
            onClick={() => {
              setDraft(displayValue(observation));
              setEditing(true);
            }}
            className="text-xs text-slate-400 hover:text-slate-700"
          >
            editar
          </button>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-1">
      <div className="flex items-center gap-1">
        <input
          autoFocus
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") void save();
            if (e.key === "Escape") setEditing(false);
          }}
          className="tabular w-40 rounded border border-slate-400 px-1.5 py-0.5 text-sm outline-none focus:ring-1 focus:ring-slate-500"
        />
        <Button variant="ghost" onClick={() => void save()} disabled={busy}>
          Salvar
        </Button>
        <Button variant="ghost" onClick={() => setEditing(false)}>
          Cancelar
        </Button>
      </div>
      {error && <div className="text-xs text-red-600">{error}</div>}
    </div>
  );
}

export function ReviewTable({
  observations,
  role,
  onRoleChange,
  onSelect,
  onSaved,
  editable,
}: {
  observations: Observation[];
  role: "A" | "B";
  onRoleChange: (role: "A" | "B") => void;
  onSelect: (observation: Observation) => void;
  onSaved: () => void;
  editable: boolean;
}) {
  const [onlyDoubtful, setOnlyDoubtful] = useState(false);
  const [query, setQuery] = useState("");

  const rows = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return observations.filter((observation) => {
      if (onlyDoubtful && observation.confidence >= LOW_CONFIDENCE) return false;
      if (!needle) return true;
      return (
        observation.raw_text.toLowerCase().includes(needle) ||
        observation.field.toLowerCase().includes(needle) ||
        subjectOf(observation).toLowerCase().includes(needle)
      );
    });
  }, [observations, onlyDoubtful, query]);

  const doubtful = observations.filter(
    (item) => item.confidence < LOW_CONFIDENCE,
  ).length;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex rounded-md bg-slate-100 p-0.5">
          {(["A", "B"] as const).map((option) => (
            <button
              key={option}
              onClick={() => onRoleChange(option)}
              className={`rounded px-3 py-1 text-xs font-medium ${
                role === option
                  ? "bg-white text-slate-900 shadow-sm"
                  : "text-slate-500"
              }`}
            >
              Documento {option}
            </button>
          ))}
        </div>

        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Filtrar por campo, vértice ou texto lido"
          className="min-w-48 flex-1 rounded-md border border-slate-300 px-2 py-1 text-sm outline-none focus:border-slate-500"
        />

        <label className="flex items-center gap-1.5 text-xs text-slate-600">
          <input
            type="checkbox"
            checked={onlyDoubtful}
            onChange={(e) => setOnlyDoubtful(e.target.checked)}
          />
          Só baixa confiança ({doubtful})
        </label>
      </div>

      {editable ? (
        <InfoNote>
          Corrigir um valor guarda o original e marca a linha como revisada por
          pessoa. A comparação é refeita a partir dos valores corrigidos, sem
          reprocessar o PDF.
        </InfoNote>
      ) : (
        <ErrorNote>Seu perfil é somente leitura: edição desabilitada.</ErrorNote>
      )}

      {rows.length === 0 ? (
        <EmptyState>Nenhuma observação para este filtro.</EmptyState>
      ) : (
        <div className="overflow-hidden rounded-md border border-slate-200 bg-white">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-3 py-2 font-medium">Onde</th>
                <th className="px-3 py-2 font-medium">Campo</th>
                <th className="px-3 py-2 font-medium">Valor</th>
                <th className="px-3 py-2 font-medium">Lido do PDF</th>
                <th className="px-3 py-2 font-medium">Confiança</th>
                <th className="px-3 py-2 font-medium">Origem</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {rows.map((observation) => (
                <tr
                  key={observation.id}
                  className={
                    observation.confidence < LOW_CONFIDENCE
                      ? "bg-amber-50/50"
                      : undefined
                  }
                >
                  <td className="tabular px-3 py-1.5 text-slate-600">
                    {subjectOf(observation)}
                  </td>
                  <td className="px-3 py-1.5 text-slate-600">
                    {FIELD_LABELS[observation.field] ?? observation.field}
                    {observation.unit && (
                      <span className="text-slate-400"> ({observation.unit})</span>
                    )}
                  </td>
                  <td className="px-3 py-1.5">
                    <EditableCell
                      observation={observation}
                      onSaved={onSaved}
                      editable={editable}
                    />
                    {observation.edited && (
                      <div className="tabular text-[11px] text-slate-400">
                        original:{" "}
                        {observation.original_value_text ??
                          num(observation.original_value_num, 6)}
                      </div>
                    )}
                  </td>
                  <td
                    className="max-w-40 truncate px-3 py-1.5 font-mono text-xs text-slate-500"
                    title={observation.raw_text}
                  >
                    {observation.raw_text}
                  </td>
                  <td className="tabular px-3 py-1.5 text-slate-600">
                    {num(observation.confidence * 100, 0)}%
                  </td>
                  <td className="px-3 py-1.5">
                    {observation.page !== null ? (
                      <button
                        onClick={() => onSelect(observation)}
                        className="text-xs text-slate-500 underline-offset-2 hover:text-slate-900 hover:underline"
                      >
                        p.{observation.page}
                        {observation.bbox ? "" : " (sem célula)"}
                      </button>
                    ) : (
                      <span className="text-xs text-slate-400">—</span>
                    )}
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
