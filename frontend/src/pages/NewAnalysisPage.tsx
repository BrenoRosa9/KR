import { useRef, useState, type FormEvent } from "react";
import { useNavigate } from "react-router";

import { api } from "../api";
import { Button, Card, ErrorNote, InfoNote } from "../components/Ui";
import { bytes } from "../format";

function FilePicker({
  label,
  hint,
  file,
  onPick,
}: {
  label: string;
  hint: string;
  file: File | null;
  onPick: (file: File | null) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);

  return (
    <div
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragging(false);
        const dropped = e.dataTransfer.files[0];
        if (dropped) onPick(dropped);
      }}
      onClick={() => inputRef.current?.click()}
      className={`cursor-pointer rounded-lg border-2 border-dashed px-4 py-6 text-center transition-colors ${
        dragging
          ? "border-slate-500 bg-slate-100"
          : "border-slate-300 hover:border-slate-400"
      }`}
    >
      <input
        ref={inputRef}
        type="file"
        accept="application/pdf,.pdf"
        className="hidden"
        onChange={(e) => onPick(e.target.files?.[0] ?? null)}
      />
      <div className="text-sm font-medium text-slate-800">{label}</div>
      {file ? (
        <div className="mt-1 text-sm text-slate-600">
          {file.name}{" "}
          <span className="text-slate-400">({bytes(file.size)})</span>
        </div>
      ) : (
        <div className="mt-1 text-xs text-slate-500">{hint}</div>
      )}
    </div>
  );
}

export default function NewAnalysisPage() {
  const navigate = useNavigate();
  const [fileA, setFileA] = useState<File | null>(null);
  const [fileB, setFileB] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!fileA || !fileB) return;
    setBusy(true);
    setError(null);
    try {
      const analysis = await api.createFromUpload(
        fileA,
        fileB,
        "exato",
        title || `${fileA.name} × ${fileB.name}`,
      );
      navigate(`/analises/${analysis.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Falha ao criar análise.");
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="mx-auto max-w-3xl space-y-4 p-6">
      <h1 className="text-lg font-semibold text-slate-900">Nova análise</h1>

      {error && <ErrorNote>{error}</ErrorNote>}

      <Card title="Documentos">
        <div className="grid gap-3 sm:grid-cols-2">
          <FilePicker
            label="Documento A"
            hint="Arraste o PDF ou clique para escolher"
            file={fileA}
            onPick={setFileA}
          />
          <FilePicker
            label="Documento B"
            hint="Arraste o PDF ou clique para escolher"
            file={fileB}
            onPick={setFileB}
          />
        </div>
        <InfoNote>
          A ordem importa apenas para a leitura do laudo: A e B são comparados
          nos dois sentidos, e cada documento também é confrontado consigo
          mesmo para separar divergência entre documentos de inconsistência
          interna. Qualquer diferença numérica é apontada — não há faixa de
          tolerância.
        </InfoNote>
      </Card>

      <Card title="Identificação">
        <label className="block space-y-1">
          <span className="text-sm font-medium text-slate-700">
            Título da análise
          </span>
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Ex.: Matrícula 12.345 — memorial x planta"
            className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm outline-none focus:border-slate-500 focus:ring-1 focus:ring-slate-500"
          />
        </label>
      </Card>

      <div className="flex justify-end gap-2">
        <Button
          type="submit"
          disabled={!fileA || !fileB || busy}
        >
          {busy ? "Enviando…" : "Comparar documentos"}
        </Button>
      </div>
    </form>
  );
}
