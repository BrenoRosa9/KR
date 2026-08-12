import { useMemo, useState } from "react";

import { EmptyState, SeverityBadge } from "./Ui";
import { FIELD_LABELS, KIND_HINTS, KIND_LABELS, num } from "../format";
import type { Finding, Provenance, Severity } from "../types";

export type FindingSelect = (finding: Finding, prefer: "A" | "B") => void;

const KIND_ORDER = [
  "systematic",
  "data_gap",
  "structural",
  "inter_document",
  "internal",
  "low_confidence",
];

function ProvenanceButton({
  role,
  provenance,
  value,
  onSelect,
}: {
  role: "A" | "B";
  provenance: Provenance | null;
  value: string | null;
  onSelect: () => void;
}) {
  if (value === null && provenance === null) return null;
  const locatable = provenance?.page != null;

  return (
    <button
      type="button"
      onClick={locatable ? onSelect : undefined}
      disabled={!locatable}
      title={
        locatable
          ? `Ver no documento ${role}, página ${provenance!.page}`
          : "Sem origem localizável no PDF"
      }
      className={`tabular inline-flex items-baseline gap-1.5 rounded px-1.5 py-0.5 text-left text-sm ${
        locatable
          ? "hover:bg-slate-100 hover:ring-1 hover:ring-slate-300"
          : "cursor-default"
      }`}
    >
      <span className="text-[10px] font-semibold text-slate-400">{role}</span>
      <span className="text-slate-800">{value ?? "—"}</span>
      {locatable && (
        <span className="text-[10px] text-slate-400">p.{provenance!.page}</span>
      )}
    </button>
  );
}

export function FindingsList({
  findings,
  onSelect,
  selectedId,
}: {
  findings: Finding[];
  onSelect: FindingSelect;
  selectedId: string | null;
}) {
  const [severityFilter, setSeverityFilter] = useState<Severity | "all">("all");

  const groups = useMemo(() => {
    const visible = findings.filter(
      (finding) =>
        severityFilter === "all" || finding.severity === severityFilter,
    );
    const byKind = new Map<string, Finding[]>();
    for (const finding of visible) {
      const list = byKind.get(finding.kind) ?? [];
      list.push(finding);
      byKind.set(finding.kind, list);
    }
    return [...byKind.entries()].sort(
      ([a], [b]) => KIND_ORDER.indexOf(a) - KIND_ORDER.indexOf(b),
    );
  }, [findings, severityFilter]);

  if (findings.length === 0) {
    return (
      <EmptyState>
        Nenhum achado registrado. Ou os documentos concordam dentro da
        tolerância, ou a comparação ainda não rodou.
      </EmptyState>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex gap-1">
        {(["all", "error", "warning", "info"] as const).map((option) => (
          <button
            key={option}
            onClick={() => setSeverityFilter(option)}
            className={`rounded px-2 py-1 text-xs font-medium ${
              severityFilter === option
                ? "bg-slate-900 text-white"
                : "bg-slate-100 text-slate-600 hover:bg-slate-200"
            }`}
          >
            {option === "all"
              ? `Todos (${findings.length})`
              : `${{ error: "Erros", warning: "Atenção", info: "Info" }[option]} (${
                  findings.filter((f) => f.severity === option).length
                })`}
          </button>
        ))}
      </div>

      {groups.map(([kind, items]) => (
        <section key={kind} className="space-y-1.5">
          <header>
            <h3 className="text-sm font-semibold text-slate-800">
              {KIND_LABELS[kind] ?? kind}{" "}
              <span className="font-normal text-slate-400">
                ({items.length})
              </span>
            </h3>
            {KIND_HINTS[kind] && (
              <p className="text-xs leading-snug text-slate-500">
                {KIND_HINTS[kind]}
              </p>
            )}
          </header>

          <ul className="space-y-1.5">
            {items.map((finding) => {
              return (
                <li
                  key={finding.id}
                  className={`rounded-md border bg-white px-3 py-2 ${
                    selectedId === finding.id
                      ? "border-slate-900 ring-1 ring-slate-900"
                      : "border-slate-200"
                  }`}
                >
                  <div className="flex items-start gap-2">
                    <SeverityBadge severity={finding.severity} />
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-baseline gap-x-2">
                        <span className="text-sm font-medium text-slate-900">
                          {finding.subject}
                        </span>
                        {finding.field && (
                          <span className="text-xs text-slate-500">
                            {FIELD_LABELS[finding.field] ?? finding.field}
                          </span>
                        )}
                      </div>
                      <p className="mt-0.5 text-[13px] leading-snug text-slate-600">
                        {finding.message}
                      </p>

                      <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1">
                        <ProvenanceButton
                          role="A"
                          provenance={finding.provenance_a}
                          value={finding.value_a}
                          onSelect={() => onSelect(finding, "A")}
                        />
                        <ProvenanceButton
                          role="B"
                          provenance={finding.provenance_b}
                          value={finding.value_b}
                          onSelect={() => onSelect(finding, "B")}
                        />
                        {finding.delta !== null && (
                          <span className="tabular text-xs text-slate-500">
                            Δ {num(finding.delta, 4)} {finding.unit}
                          </span>
                        )}
                      </div>
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>
        </section>
      ))}
    </div>
  );
}
