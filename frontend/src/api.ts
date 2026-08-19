import type {
  Analysis,
  AnalysisDetail,
  DocumentInfo,
  Observation,
  ToleranceProfile,
} from "./types";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      ...(init.body instanceof FormData
        ? {}
        : { "Content-Type": "application/json" }),
      ...init.headers,
    },
  });

  if (response.status === 204) {
    return undefined as T;
  }

  const text = await response.text();
  const payload = text ? JSON.parse(text) : null;

  if (!response.ok) {
    const detail =
      (payload && (payload.detail ?? payload.message)) ??
      `Falha na requisição (${response.status}).`;
    throw new ApiError(
      response.status,
      typeof detail === "string" ? detail : JSON.stringify(detail),
    );
  }

  return payload as T;
}

export const api = {
  profiles: () => request<ToleranceProfile[]>("/api/profiles"),

  history: (limit = 50) =>
    request<Analysis[]>(`/api/analyses?limit=${limit}`),

  analysis: (id: string) => request<AnalysisDetail>(`/api/analyses/${id}`),

  observations: (
    id: string,
    options: { role?: "A" | "B"; lowConfidenceOnly?: boolean } = {},
  ) => {
    const query = new URLSearchParams();
    if (options.role) query.set("role", options.role);
    if (options.lowConfidenceOnly) query.set("low_confidence_only", "true");
    const suffix = query.toString() ? `?${query}` : "";
    return request<Observation[]>(`/api/analyses/${id}/observations${suffix}`);
  },

  createFromUpload: (
    fileA: File,
    fileB: File,
    profile: string,
    title: string,
  ) => {
    const body = new FormData();
    body.append("file_a", fileA);
    body.append("file_b", fileB);
    const query = new URLSearchParams({ profile, title });
    return request<Analysis>(`/api/analyses/upload?${query}`, {
      method: "POST",
      body,
    });
  },

  updateObservation: (
    id: string,
    value: { value_num?: number; value_text?: string },
    recompare = true,
  ) =>
    request<Observation>(`/api/observations/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ ...value, recompare }),
    }),

  updateCrs: (
    extractionId: string,
    payload: {
      epsg: string;
      datum_label: string;
      utm_zone?: number | null;
      hemisphere?: string | null;
      distances_are_ground?: boolean;
      average_height_m?: number;
    },
  ) =>
    request(`/api/extractions/${extractionId}/crs`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),

  recompare: (id: string, profile?: string) =>
    request<Analysis>(
      `/api/analyses/${id}/recompare${profile ? `?profile=${profile}` : ""}`,
      { method: "POST" },
    ),

  document: (id: string) => request<DocumentInfo>(`/api/documents/${id}`),

  documentUrl: (id: string) => `/api/documents/${id}/file`,

  reportHtmlUrl: (id: string) => `/api/analyses/${id}/report.html`,

  reportPdfUrl: (id: string) => `/api/analyses/${id}/report.pdf`,
};
