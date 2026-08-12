import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from "react";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";

import { api } from "../api";
import type { BBox } from "../types";
import { Button, Spinner } from "./Ui";

// Worker servido de /public com a mesma versão do pdfjs da API. O query string
// invalida cache do navegador quando a versão muda (era a causa do mismatch
// 5.4.296 vs 6.2.108 após downgrade do pacote).
pdfjs.GlobalWorkerOptions.workerSrc = `/pdf.worker.min.mjs?v=${pdfjs.version}`;

const MIN_ZOOM = 0.5;
const MAX_ZOOM = 5;
const ZOOM_STEP = 0.25;
const WHEEL_ZOOM_STEP = 0.12;

export interface Highlight {
  page: number;
  bbox: BBox | null;
  label?: string;
}

function clampZoom(value: number): number {
  return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, value));
}

/**
 * Visualizador de uma página por vez.
 *
 * Documentos de georreferenciamento chegam com dezenas de páginas e tabelas
 * enormes; renderizar tudo trava o navegador. Como a navegação aqui é sempre
 * dirigida por uma proveniência ("vá para a página 4, célula tal"), mostrar
 * só a página pedida é suficiente e mantém a interface fluida.
 *
 * Com zoom alto, o usuário arrasta para panear e usa a roda do mouse para
 * ampliar — o scroll nativo sozinho não dá conta de plantas A1.
 */
export function PdfViewer({
  documentId,
  highlight,
  title,
}: {
  documentId: string;
  highlight: Highlight | null;
  title: string;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const pageRef = useRef<HTMLDivElement>(null);
  const overlayRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<{
    active: boolean;
    pointerId: number;
    x: number;
    y: number;
    moved: boolean;
  } | null>(null);
  const pendingFocusRef = useRef<{
    relX: number;
    relY: number;
    clientX: number;
    clientY: number;
  } | null>(null);

  const [width, setWidth] = useState(680);
  const [zoom, setZoom] = useState(1);
  const [pageCount, setPageCount] = useState(0);
  const [pageNumber, setPageNumber] = useState(1);
  const [pointsPerPixel, setPointsPerPixel] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);

  useLayoutEffect(() => {
    const element = containerRef.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => {
      if (entry) setWidth(Math.max(280, entry.contentRect.width - 24));
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (highlight) setPageNumber(highlight.page);
  }, [highlight]);

  // Rolar o destaque para o centro só faz sentido depois que a página rendeu
  // e que sabemos a escala; por isso depende de pointsPerPixel.
  useEffect(() => {
    if (!highlight?.bbox || pointsPerPixel === null) return;
    if (pendingFocusRef.current) return;
    overlayRef.current?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [highlight, pointsPerPixel, pageNumber]);

  // Após o zoom, recoloca o ponto do conteúdo que estava sob o cursor
  // (funciona mesmo com a página centralizada no wrapper).
  useLayoutEffect(() => {
    const focus = pendingFocusRef.current;
    const container = containerRef.current;
    const page = pageRef.current;
    if (!focus || !container || !page) return;

    const pageRect = page.getBoundingClientRect();
    const pointX = pageRect.left + focus.relX * pageRect.width;
    const pointY = pageRect.top + focus.relY * pageRect.height;
    container.scrollLeft += pointX - focus.clientX;
    container.scrollTop += pointY - focus.clientY;
    pendingFocusRef.current = null;
  }, [zoom]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const page = pageRef.current;
      const direction = event.deltaY > 0 ? -1 : 1;
      const factor =
        event.ctrlKey || event.metaKey ? WHEEL_ZOOM_STEP * 1.5 : WHEEL_ZOOM_STEP;

      setZoom((current) => {
        const next = clampZoom(current + direction * factor);
        if (next === current) return current;

        if (page) {
          const pageRect = page.getBoundingClientRect();
          const width = pageRect.width || 1;
          const height = pageRect.height || 1;
          pendingFocusRef.current = {
            relX: (event.clientX - pageRect.left) / width,
            relY: (event.clientY - pageRect.top) / height,
            clientX: event.clientX,
            clientY: event.clientY,
          };
        }
        return next;
      });
    };

    // passive: false é obrigatório para poder cancelar o scroll da página.
    container.addEventListener("wheel", onWheel, { passive: false });
    return () => container.removeEventListener("wheel", onWheel);
  }, []);

  function applyZoom(next: number) {
    const container = containerRef.current;
    const page = pageRef.current;
    setZoom((current) => {
      const clamped = clampZoom(next);
      if (clamped === current || !container) return clamped;

      if (page) {
        const pageRect = page.getBoundingClientRect();
        const containerRect = container.getBoundingClientRect();
        const clientX = containerRect.left + container.clientWidth / 2;
        const clientY = containerRect.top + container.clientHeight / 2;
        const width = pageRect.width || 1;
        const height = pageRect.height || 1;
        pendingFocusRef.current = {
          relX: (clientX - pageRect.left) / width,
          relY: (clientY - pageRect.top) / height,
          clientX,
          clientY,
        };
      }
      return clamped;
    });
  }

  function onPointerDown(event: ReactPointerEvent<HTMLDivElement>) {
    if (event.button !== 0) return;
    const container = containerRef.current;
    if (!container) return;
    dragRef.current = {
      active: true,
      pointerId: event.pointerId,
      x: event.clientX,
      y: event.clientY,
      moved: false,
    };
    container.setPointerCapture(event.pointerId);
    setDragging(true);
  }

  function onPointerMove(event: ReactPointerEvent<HTMLDivElement>) {
    const drag = dragRef.current;
    const container = containerRef.current;
    if (!drag?.active || !container) return;
    if (event.pointerId !== drag.pointerId) return;

    const dx = event.clientX - drag.x;
    const dy = event.clientY - drag.y;
    if (!drag.moved && dx * dx + dy * dy > 4) drag.moved = true;
    container.scrollLeft -= dx;
    container.scrollTop -= dy;
    drag.x = event.clientX;
    drag.y = event.clientY;
  }

  function endDrag(event: ReactPointerEvent<HTMLDivElement>) {
    const drag = dragRef.current;
    const container = containerRef.current;
    if (!drag) return;
    if (event.pointerId !== drag.pointerId) return;
    dragRef.current = null;
    setDragging(false);
    if (container?.hasPointerCapture(event.pointerId)) {
      container.releasePointerCapture(event.pointerId);
    }
  }

  const scale = pointsPerPixel === null ? 0 : 1 / pointsPerPixel;
  const box = highlight?.bbox;
  const showBox = box && box.length === 4 && highlight.page === pageNumber;

  return (
    <div className="flex h-full flex-col rounded-lg border border-slate-200 bg-white">
      <header className="flex items-center justify-between gap-2 border-b border-slate-200 px-3 py-2">
        <div className="min-w-0">
          <span
            className="block truncate text-sm font-medium text-slate-700"
            title={title}
          >
            {title}
          </span>
          <span className="text-[11px] text-slate-400">
            Arraste para mover · scroll para zoom
          </span>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <Button
            variant="ghost"
            onClick={() => setPageNumber((p) => Math.max(1, p - 1))}
            disabled={pageNumber <= 1}
          >
            ‹
          </Button>
          <span className="tabular px-1 text-xs text-slate-600">
            {pageNumber} / {pageCount || "—"}
          </span>
          <Button
            variant="ghost"
            onClick={() => setPageNumber((p) => Math.min(pageCount, p + 1))}
            disabled={pageNumber >= pageCount}
          >
            ›
          </Button>
          <Button
            variant="ghost"
            onClick={() => applyZoom(zoom - ZOOM_STEP)}
            disabled={zoom <= MIN_ZOOM}
          >
            −
          </Button>
          <button
            type="button"
            title="Voltar a 100%"
            onClick={() => applyZoom(1)}
            className="tabular rounded px-1 text-xs text-slate-600 hover:bg-slate-100"
          >
            {Math.round(zoom * 100)}%
          </button>
          <Button
            variant="ghost"
            onClick={() => applyZoom(zoom + ZOOM_STEP)}
            disabled={zoom >= MAX_ZOOM}
          >
            +
          </Button>
        </div>
      </header>

      <div
        ref={containerRef}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        className={`flex-1 overflow-auto bg-slate-100 p-3 ${
          dragging ? "cursor-grabbing select-none" : "cursor-grab"
        }`}
      >
        {error ? (
          <div className="cursor-default p-4 text-sm text-red-700">{error}</div>
        ) : (
          // w-max + min-w-full: quando o PDF é mais largo que a viewport (zoom),
          // o wrapper cresce junto e o scroll alcança as duas bordas. Com
          // justify-center puro o overflow à esquerda ficava inacessível —
          // a barra ia a 0 e a tabela ainda cortava.
          <div className="inline-flex min-h-full min-w-full justify-center">
            <Document
              file={api.documentUrl(documentId)}
              onLoadSuccess={({ numPages }) => {
                setPageCount(numPages);
                setError(null);
              }}
              onLoadError={(err) =>
                setError(`Não foi possível abrir o PDF: ${err.message}`)
              }
              loading={
                <div className="p-4">
                  <Spinner label="Carregando documento…" />
                </div>
              }
            >
              <div ref={pageRef} className="relative shadow-md">
                <Page
                  pageNumber={pageNumber}
                  width={width * zoom}
                  renderAnnotationLayer={false}
                  renderTextLayer={false}
                  onLoadSuccess={(page) =>
                    setPointsPerPixel(page.originalWidth / page.width)
                  }
                  loading={
                    <div className="p-4">
                      <Spinner label="Renderizando página…" />
                    </div>
                  }
                />
                {showBox && (
                  <div
                    ref={overlayRef}
                    className="pointer-events-none absolute rounded-[2px] bg-amber-300/30 ring-2 ring-amber-500"
                    style={{
                      left: box[0]! * scale - 2,
                      top: box[1]! * scale - 2,
                      width: (box[2]! - box[0]!) * scale + 4,
                      height: (box[3]! - box[1]!) * scale + 4,
                    }}
                  >
                    {highlight.label && (
                      <span className="absolute -top-5 left-0 whitespace-nowrap rounded bg-amber-500 px-1 text-[10px] font-medium text-white">
                        {highlight.label}
                      </span>
                    )}
                  </div>
                )}
              </div>
            </Document>
          </div>
        )}
      </div>

      {highlight && !highlight.bbox && (
        <footer className="border-t border-slate-200 bg-amber-50 px-3 py-1.5 text-xs text-amber-800">
          Este valor veio do texto corrido, sem célula de tabela associada. A
          página está indicada, mas não há região exata para destacar.
        </footer>
      )}
    </div>
  );
}
