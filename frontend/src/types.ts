export type Severity = "error" | "warning" | "info";

export type FindingKind =
  | "systematic"
  | "inter_document"
  | "internal"
  | "structural"
  | "low_confidence"
  | "data_gap";

export interface User {
  id: string;
  email: string;
  name: string;
  role: "admin" | "analyst" | "viewer";
}

/** bbox em pontos do PDF, convenção do pdfplumber: [x0, top, x1, bottom],
 *  com origem no canto superior esquerdo da página. */
export type BBox = number[];

export interface Provenance {
  page: number | null;
  bbox: BBox | null;
  source_kind: string | null;
  table_index: number | null;
  row: number | null;
  column: number | null;
  raw_text: string | null;
  label: string | null;
}

export interface DocumentInfo {
  id: string;
  filename: string;
  sha256: string;
  size_bytes: number;
  page_count: number | null;
  producer: string | null;
  triage: TriageInfo | null;
  created_at: string;
}

export interface TriageInfo {
  dominant_class?: string;
  needs_ocr?: boolean;
  pages?: Array<{
    number: number;
    classification: string;
    relevance: string;
    char_count: number;
    image_count: number;
    vector_count: number;
    table_candidates: number;
    notes: string[];
  }>;
}

export interface Analysis {
  id: string;
  title: string;
  status:
    | "pending"
    | "extracting"
    | "awaiting_review"
    | "compared"
    | "failed";
  profile_name: string;
  summary: Record<string, number> | null;
  error: string | null;
  created_at: string;
  compared_at: string | null;
}

export interface Finding {
  id: string;
  kind: FindingKind;
  severity: Severity;
  field: string | null;
  subject: string;
  message: string;
  value_a: string | null;
  value_b: string | null;
  delta: number | null;
  tolerance: number | null;
  unit: string;
  scope: string;
  provenance_a: Provenance | null;
  provenance_b: Provenance | null;
}

export interface Extraction {
  id: string;
  document_id: string;
  role: "A" | "B";
  label: string;
  crs_epsg: string | null;
  datum_label: string | null;
  utm_zone: number | null;
  hemisphere: string | null;
  number_convention: string;
  distances_are_ground: boolean;
  average_height_m: number;
  source_page: number | null;
  table_strategy: string | null;
  stages: Array<{ stage: string; ok: boolean; message: string }> | null;
  warnings: string[] | null;
  errors: string[] | null;
}

export interface Observation {
  id: string;
  field: string;
  vertex_index: number | null;
  segment_index: number | null;
  value_num: number | null;
  value_text: string | null;
  unit: string;
  halfwidth: number;
  confidence: number;
  page: number | null;
  bbox: BBox | null;
  source_kind: string;
  raw_text: string;
  edited: boolean;
  original_value_num: number | null;
  original_value_text: string | null;
  edited_at: string | null;
}

export interface RecomputedSummary {
  area_m2: number | null;
  perimeter_grid_m: number | null;
  perimeter_ground_m: number | null;
  scale_factor: number;
  closure_linear_error: number | null;
  closure_precision: number | null;
  notes: string[];
}

export interface MatchSummary {
  method?: string;
  pairs?: number;
  unmatched_a?: number[];
  unmatched_b?: number[];
  reversed_orientation?: boolean;
  rotation_offset?: number;
  notes?: string[];
  systematic?: {
    kind: string;
    magnitude: number;
    azimuth: number;
    residual_rms: number;
    message: string;
  } | null;
  recomputed_a?: RecomputedSummary | null;
  recomputed_b?: RecomputedSummary | null;
}

export interface AnalysisDetail {
  analysis: Analysis;
  documents: { A: DocumentInfo; B: DocumentInfo };
  extractions: Extraction[];
  findings: Finding[];
  match: MatchSummary | null;
}

export interface ToleranceProfile {
  key: string;
  name: string;
  coordinate_m: number;
  distance_m: number;
  distance_ppm: number;
  azimuth_arcsec: number;
  angle_arcsec: number;
  area_m2: number;
  area_relative: number;
  perimeter_m: number;
  min_closure_precision: number;
}
