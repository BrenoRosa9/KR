/** Formatação em pt-BR. Todo número exibido passa por aqui. */

const NUMBER_CACHE = new Map<number, Intl.NumberFormat>();

function formatter(decimals: number): Intl.NumberFormat {
  let cached = NUMBER_CACHE.get(decimals);
  if (!cached) {
    cached = new Intl.NumberFormat("pt-BR", {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    });
    NUMBER_CACHE.set(decimals, cached);
  }
  return cached;
}

export function num(
  value: number | null | undefined,
  decimals = 3,
): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return formatter(decimals).format(value);
}

export function dms(degrees: number | null | undefined): string {
  if (degrees === null || degrees === undefined) return "—";
  const sign = degrees < 0 ? "-" : "";
  const total = Math.abs(degrees);
  let d = Math.floor(total);
  let m = Math.floor((total - d) * 60);
  let s = ((total - d) * 60 - m) * 60;
  // Arredondar os segundos pode transbordar para minuto e grau.
  if (Number(s.toFixed(2)) >= 60) {
    s = 0;
    m += 1;
  }
  if (m >= 60) {
    m = 0;
    d += 1;
  }
  return `${sign}${d}°${String(m).padStart(2, "0")}'${num(s, 2).padStart(5, "0")}"`;
}

export function bytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${num(value / 1024, 0)} KB`;
  return `${num(value / (1024 * 1024), 1)} MB`;
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  return new Intl.DateTimeFormat("pt-BR", {
    dateStyle: "short",
    timeStyle: "short",
  }).format(date);
}

export const FIELD_LABELS: Record<string, string> = {
  vertex_code: "Vértice",
  easting: "Coordenada E",
  northing: "Coordenada N",
  latitude: "Latitude",
  longitude: "Longitude",
  azimuth: "Azimute",
  distance: "Distância",
  interior_angle: "Ângulo interno",
  arc_radius: "Raio da curva",
  arc_development: "Desenvolvimento da curva",
  central_angle: "Ângulo central",
  confrontant: "Confrontante",
  area: "Área",
  perimeter: "Perímetro",
  matricula: "Matrícula",
  cpf: "CPF",
  cnpj: "CNPJ",
  utm_zone: "Fuso",
  datum: "Datum",
};

export const KIND_LABELS: Record<string, string> = {
  systematic: "Padrão sistemático",
  inter_document: "Divergência entre documentos",
  internal: "Inconsistência interna",
  structural: "Estrutura",
  low_confidence: "Baixa confiança",
  data_gap: "Lacuna de dados",
};

export const KIND_HINTS: Record<string, string> = {
  systematic:
    "Um único parâmetro explica quase toda a diferença. Trate a causa antes de olhar os vértices um a um.",
  inter_document:
    "Os dois documentos afirmam coisas diferentes sobre a mesma grandeza.",
  internal:
    "O documento discorda de si mesmo: o valor impresso não confere com o que suas próprias coordenadas produzem.",
  structural:
    "A geometria descrita não tem a mesma forma nos dois documentos.",
  low_confidence:
    "Valor lido com baixa confiança. Confira contra o original antes de emitir o laudo.",
  data_gap:
    "Falta informação essencial. Nada é assumido no lugar dela.",
};

export const STATUS_LABELS: Record<string, string> = {
  pending: "Na fila",
  extracting: "Extraindo",
  awaiting_review: "Aguardando revisão",
  compared: "Comparado",
  failed: "Falhou",
};
