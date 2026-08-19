"""Contratos da API."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    sha256: str
    size_bytes: int
    page_count: int | None
    producer: str | None
    triage: dict | None
    created_at: datetime


class AnalysisCreate(BaseModel):
    document_a_id: uuid.UUID
    document_b_id: uuid.UUID
    profile: str = "exato"
    title: str = ""


class AnalysisOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    status: str
    profile_name: str
    summary: dict | None
    error: str | None
    created_at: datetime
    compared_at: datetime | None


class ProvenanceOut(BaseModel):
    page: int | None = None
    bbox: list[float] | None = None
    source_kind: str | None = None
    table_index: int | None = None
    row: int | None = None
    column: int | None = None
    raw_text: str | None = None
    label: str | None = None


class FindingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    severity: str
    field: str | None
    subject: str
    message: str
    value_a: str | None
    value_b: str | None
    delta: float | None
    tolerance: float | None
    unit: str
    scope: str
    provenance_a: dict | None
    provenance_b: dict | None


class ExtractionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_id: uuid.UUID
    role: str
    label: str
    crs_epsg: str | None
    datum_label: str | None
    utm_zone: int | None
    hemisphere: str | None
    number_convention: str
    distances_are_ground: bool
    average_height_m: float
    source_page: int | None
    table_strategy: str | None
    stages: list | None
    warnings: list | None
    errors: list | None


class ObservationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    field: str
    vertex_index: int | None
    segment_index: int | None
    value_num: float | None
    value_text: str | None
    unit: str
    halfwidth: float
    confidence: float
    page: int | None
    bbox: list[float] | None
    source_kind: str
    raw_text: str
    edited: bool
    original_value_num: float | None
    original_value_text: str | None
    edited_at: datetime | None


class ObservationUpdate(BaseModel):
    value_num: float | None = None
    value_text: str | None = None
    recompare: bool = True


class CRSUpdate(BaseModel):
    epsg: str = Field(min_length=4, max_length=20)
    datum_label: str = Field(min_length=1, max_length=60)
    utm_zone: int | None = Field(default=None, ge=1, le=60)
    hemisphere: str | None = Field(default=None, pattern="^[NS]$")
    distances_are_ground: bool | None = None
    average_height_m: float | None = Field(default=None, ge=-500.0, le=6000.0)


class AnalysisDetail(BaseModel):
    analysis: AnalysisOut
    documents: dict[str, DocumentOut]
    extractions: list[ExtractionOut]
    findings: list[FindingOut]
    match: dict | None


class ProfileOut(BaseModel):
    key: str
    name: str
    coordinate_m: float
    distance_m: float
    distance_ppm: float
    azimuth_arcsec: float
    angle_arcsec: float
    area_m2: float
    area_relative: float
    perimeter_m: float
    min_closure_precision: float


class HealthOut(BaseModel):
    status: str
    database: bool
    queue: dict[str, int]
    version: str
