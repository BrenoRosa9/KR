"""Tradução entre o banco e o núcleo determinístico.

Duas direções:

* ``persist_extraction`` grava um :class:`Parcel` como linhas de
  :class:`Observation`;
* ``parcel_from_extraction`` reconstrói o :class:`Parcel` a partir dessas linhas.

O caminho de volta é o que faz a revisão humana funcionar: um analista corrige
uma célula, a observação é atualizada, e o recálculo seguinte usa o valor
corrigido sem que nada mais precise saber que houve edição.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .core.compare import ComparisonResult
from .core.compare import Finding as CoreFinding
from .core.crs import CRSSpec
from .core.schema import (
    FieldKind,
    Measured,
    Parcel,
    Provenance,
    Segment,
    SourceKind,
    TextValue,
    Vertex,
)
from .core.tolerance import ToleranceProfile
from .extraction.pipeline import ExtractionResult
from .models import Analysis, Extraction, Finding, Observation

# Campos que pertencem ao imóvel como um todo, não a um vértice ou lado.
PARCEL_FIELDS = {FieldKind.AREA, FieldKind.PERIMETER, FieldKind.MATRICULA}


def persist_extraction(
    session: Session,
    analysis_id: uuid.UUID,
    document_id: uuid.UUID,
    role: str,
    result: ExtractionResult,
) -> Extraction:
    """Grava o resultado da extração como observações individuais."""
    parcel = result.parcel
    context = result.context
    crs = parcel.crs if parcel else (context.crs if context else None)

    extraction = Extraction(
        analysis_id=analysis_id,
        document_id=document_id,
        role=role,
        label=parcel.label if parcel else "",
        crs_epsg=crs.epsg if crs else None,
        datum_label=crs.datum_label if crs else None,
        utm_zone=crs.utm_zone if crs else None,
        hemisphere=crs.hemisphere if crs else None,
        number_convention=context.convention if context else "br",
        distances_are_ground=parcel.distances_are_ground if parcel else True,
        average_height_m=parcel.average_height_m if parcel else 0.0,
        source_page=result.chosen.table.page if result.chosen else None,
        table_strategy=result.chosen.table.strategy if result.chosen else None,
        stages=[
            {"stage": str(log.stage), "ok": log.ok, "message": log.message}
            for log in result.stages
        ],
        warnings=list(result.warnings),
        errors=list(result.errors),
    )
    session.add(extraction)
    session.flush()

    if parcel is not None:
        _write_observations(session, extraction, parcel)
    return extraction


def _write_observations(
    session: Session, extraction: Extraction, parcel: Parcel
) -> None:
    ordinal = 0

    for index, vertex in enumerate(parcel.vertices):
        if vertex.code:
            session.add(
                _text_observation(
                    extraction,
                    FieldKind.VERTEX_CODE,
                    TextValue(value=vertex.code, provenance=vertex.code_provenance),
                    ordinal,
                    vertex_index=index,
                )
            )
            ordinal += 1
        for field_kind, measured in (
            (FieldKind.EASTING, vertex.easting),
            (FieldKind.NORTHING, vertex.northing),
            (FieldKind.LONGITUDE, vertex.longitude),
            (FieldKind.LATITUDE, vertex.latitude),
        ):
            if measured is None:
                continue
            session.add(
                _numeric_observation(
                    extraction, field_kind, measured, ordinal, vertex_index=index
                )
            )
            ordinal += 1

    for index, segment in enumerate(parcel.segments):
        for field_kind, measured in (
            (FieldKind.AZIMUTH, segment.azimuth),
            (FieldKind.DISTANCE, segment.distance),
            (FieldKind.ARC_RADIUS, segment.arc_radius),
            (FieldKind.ARC_DEVELOPMENT, segment.arc_development),
            (FieldKind.CENTRAL_ANGLE, segment.central_angle),
        ):
            if measured is None:
                continue
            session.add(
                _numeric_observation(
                    extraction,
                    field_kind,
                    measured,
                    ordinal,
                    vertex_index=segment.from_index,
                    segment_index=index,
                )
            )
            ordinal += 1
        if segment.confrontant is not None:
            session.add(
                _text_observation(
                    extraction,
                    FieldKind.CONFRONTANT,
                    segment.confrontant,
                    ordinal,
                    vertex_index=segment.from_index,
                    segment_index=index,
                )
            )
            ordinal += 1

    for field_kind, measured in (
        (FieldKind.AREA, parcel.area),
        (FieldKind.PERIMETER, parcel.perimeter),
    ):
        if measured is None:
            continue
        session.add(_numeric_observation(extraction, field_kind, measured, ordinal))
        ordinal += 1

    if parcel.matricula is not None:
        session.add(
            _text_observation(extraction, FieldKind.MATRICULA, parcel.matricula, ordinal)
        )
        ordinal += 1

    for tax_id in parcel.tax_ids:
        field = FieldKind.CNPJ if len(tax_id.value) == 14 else FieldKind.CPF
        session.add(_text_observation(extraction, field, tax_id, ordinal))
        ordinal += 1

    # Citações alternativas de área/perímetro (texto descritivo divergente).
    # A principal já foi gravada acima; as demais entram com o mesmo field
    # para a reconstrução e a revisão humana.
    primary_area = parcel.area.value if parcel.area else None
    for citation in parcel.area_citations:
        if primary_area is not None and abs(citation.value - primary_area) < 1e-6:
            continue
        session.add(_numeric_observation(extraction, FieldKind.AREA, citation, ordinal))
        ordinal += 1

    primary_perimeter = parcel.perimeter.value if parcel.perimeter else None
    for citation in parcel.perimeter_citations:
        if (
            primary_perimeter is not None
            and abs(citation.value - primary_perimeter) < 1e-6
        ):
            continue
        session.add(
            _numeric_observation(extraction, FieldKind.PERIMETER, citation, ordinal)
        )
        ordinal += 1


def _numeric_observation(
    extraction: Extraction,
    field_kind: FieldKind,
    measured: Measured,
    ordinal: int,
    vertex_index: int | None = None,
    segment_index: int | None = None,
) -> Observation:
    provenance = measured.provenance
    return Observation(
        extraction_id=extraction.id,
        field=str(field_kind),
        vertex_index=vertex_index,
        segment_index=segment_index,
        ordinal=ordinal,
        value_num=measured.value,
        unit=measured.unit,
        halfwidth=measured.halfwidth,
        confidence=measured.confidence,
        page=provenance.page if provenance else None,
        bbox=list(provenance.bbox) if provenance and provenance.bbox else None,
        source_kind=str(provenance.source_kind) if provenance else "computed",
        table_index=provenance.table_index if provenance else None,
        row=provenance.row if provenance else None,
        column=provenance.column if provenance else None,
        raw_text=provenance.raw_text if provenance else "",
    )


def _text_observation(
    extraction: Extraction,
    field_kind: FieldKind,
    value: TextValue,
    ordinal: int,
    vertex_index: int | None = None,
    segment_index: int | None = None,
) -> Observation:
    provenance = value.provenance
    return Observation(
        extraction_id=extraction.id,
        field=str(field_kind),
        vertex_index=vertex_index,
        segment_index=segment_index,
        ordinal=ordinal,
        value_text=value.value,
        confidence=value.confidence,
        page=provenance.page if provenance else None,
        bbox=list(provenance.bbox) if provenance and provenance.bbox else None,
        source_kind=str(provenance.source_kind) if provenance else "computed",
        table_index=provenance.table_index if provenance else None,
        row=provenance.row if provenance else None,
        column=provenance.column if provenance else None,
        raw_text=provenance.raw_text if provenance else "",
    )


def parcel_from_extraction(session: Session, extraction: Extraction) -> Parcel:
    """Reconstrói o imóvel a partir das observações gravadas."""
    observations = session.scalars(
        select(Observation)
        .where(Observation.extraction_id == extraction.id)
        .order_by(Observation.ordinal)
    ).all()

    parcel = Parcel(
        label=extraction.label,
        crs=_crs_from_extraction(extraction),
        distances_are_ground=extraction.distances_are_ground,
        average_height_m=extraction.average_height_m,
    )

    vertex_count = max(
        (o.vertex_index for o in observations if o.vertex_index is not None),
        default=-1,
    ) + 1
    parcel.vertices = [Vertex(code=f"#{i + 1}") for i in range(vertex_count)]

    segment_count = max(
        (o.segment_index for o in observations if o.segment_index is not None),
        default=-1,
    ) + 1
    parcel.segments = [
        Segment(from_index=i, to_index=(i + 1) % max(1, vertex_count))
        for i in range(segment_count)
    ]

    for observation in observations:
        _apply_observation(parcel, observation)

    parcel.warnings = list(extraction.warnings or [])
    return parcel


_SEGMENT_ATTRIBUTES = {
    FieldKind.AZIMUTH: "azimuth",
    FieldKind.DISTANCE: "distance",
    FieldKind.ARC_RADIUS: "arc_radius",
    FieldKind.ARC_DEVELOPMENT: "arc_development",
    FieldKind.CENTRAL_ANGLE: "central_angle",
}


def _apply_observation(parcel: Parcel, observation: Observation) -> None:
    field_kind = observation.field
    provenance = _provenance_from(observation)

    if field_kind == FieldKind.VERTEX_CODE:
        if observation.vertex_index is not None and observation.value_text:
            vertex = parcel.vertices[observation.vertex_index]
            vertex.code = observation.value_text
            vertex.code_provenance = provenance
        return

    if field_kind in {
        FieldKind.EASTING,
        FieldKind.NORTHING,
        FieldKind.LONGITUDE,
        FieldKind.LATITUDE,
    }:
        if observation.vertex_index is None or observation.value_num is None:
            return
        vertex = parcel.vertices[observation.vertex_index]
        setattr(
            vertex,
            _vertex_attribute(field_kind),
            _measured_from(observation, provenance),
        )
        return

    if field_kind in _SEGMENT_ATTRIBUTES:
        if observation.segment_index is None or observation.value_num is None:
            return
        segment = parcel.segments[observation.segment_index]
        setattr(
            segment,
            _SEGMENT_ATTRIBUTES[field_kind],
            _measured_from(observation, provenance),
        )
        return

    if field_kind == FieldKind.CONFRONTANT:
        if observation.segment_index is None or not observation.value_text:
            return
        parcel.segments[observation.segment_index].confrontant = TextValue(
            value=observation.value_text,
            confidence=observation.confidence,
            provenance=provenance,
            edited=observation.edited,
        )
        return

    if field_kind == FieldKind.AREA and observation.value_num is not None:
        measured = _measured_from(observation, provenance)
        if parcel.area is None:
            parcel.area = measured
        parcel.area_citations.append(measured)
    elif field_kind == FieldKind.PERIMETER and observation.value_num is not None:
        measured = _measured_from(observation, provenance)
        if parcel.perimeter is None:
            parcel.perimeter = measured
        parcel.perimeter_citations.append(measured)
    elif field_kind == FieldKind.MATRICULA and observation.value_text:
        parcel.matricula = TextValue(
            value=observation.value_text,
            confidence=observation.confidence,
            provenance=provenance,
            edited=observation.edited,
        )
    elif field_kind in {FieldKind.CPF, FieldKind.CNPJ} and observation.value_text:
        parcel.tax_ids.append(
            TextValue(
                value=observation.value_text,
                confidence=observation.confidence,
                provenance=provenance,
                edited=observation.edited,
            )
        )


def _vertex_attribute(field_kind: str) -> str:
    return {
        FieldKind.EASTING: "easting",
        FieldKind.NORTHING: "northing",
        FieldKind.LONGITUDE: "longitude",
        FieldKind.LATITUDE: "latitude",
    }[field_kind]  # type: ignore[index]


def _measured_from(observation: Observation, provenance: Provenance | None) -> Measured:
    return Measured(
        value=float(observation.value_num or 0.0),
        halfwidth=observation.halfwidth,
        unit=observation.unit,
        confidence=observation.confidence,
        provenance=provenance,
        edited=observation.edited,
    )


def _provenance_from(observation: Observation) -> Provenance | None:
    if observation.page is None and not observation.raw_text:
        return None
    bbox = tuple(observation.bbox) if observation.bbox else None
    try:
        source_kind = SourceKind(observation.source_kind)
    except ValueError:  # pragma: no cover - dado legado
        source_kind = SourceKind.TABLE_CELL
    return Provenance(
        document_id=str(observation.extraction_id),
        page=observation.page or 0,
        bbox=bbox,  # type: ignore[arg-type]
        source_kind=source_kind,
        table_index=observation.table_index,
        row=observation.row,
        column=observation.column,
        raw_text=observation.raw_text,
    )


def _crs_from_extraction(extraction: Extraction) -> CRSSpec | None:
    if not extraction.crs_epsg:
        return None
    return CRSSpec(
        epsg=extraction.crs_epsg,
        datum_label=extraction.datum_label or "",
        utm_zone=extraction.utm_zone,
        hemisphere=extraction.hemisphere,  # type: ignore[arg-type]
    )


def persist_comparison(
    session: Session,
    analysis: Analysis,
    result: ComparisonResult,
    profile: ToleranceProfile,
) -> None:
    """Substitui os achados da análise pelos da execução atual.

    Recomparar é idempotente de propósito: depois de uma correção humana o
    relatório é regerado do zero, sem achados fantasma da execução anterior.
    """
    for existing in list(analysis.findings):
        session.delete(existing)
    session.flush()

    for ordinal, finding in enumerate(_ordered(result.findings)):
        session.add(
            Finding(
                analysis_id=analysis.id,
                kind=str(finding.kind),
                severity=str(finding.severity),
                field=str(finding.field) if finding.field else None,
                subject=finding.subject[:255],
                message=finding.message,
                value_a=_as_text(finding.value_a),
                value_b=_as_text(finding.value_b),
                delta=finding.delta,
                tolerance=finding.tolerance,
                unit=finding.unit,
                scope=finding.scope,
                provenance_a=_provenance_dict(finding.provenance_a),
                provenance_b=_provenance_dict(finding.provenance_b),
                ordinal=ordinal,
            )
        )

    analysis.summary = dict(result.summary)
    # A chave, nunca o rótulo: é por ela que a recomparação encontra o perfil.
    analysis.profile_name = profile.key
    analysis.profile_snapshot = profile.__dict__.copy()
    analysis.match_summary = _match_summary(result)
    analysis.compared_at = datetime.now(UTC)


_SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}
_KIND_ORDER = {
    "systematic": 0,
    "data_gap": 1,
    "structural": 2,
    "inter_document": 3,
    "internal": 4,
    "low_confidence": 5,
}


def _ordered(findings: list[CoreFinding]) -> list[CoreFinding]:
    """Achado sistemático primeiro: é o que explica todos os outros."""
    return sorted(
        findings,
        key=lambda finding: (
            _SEVERITY_ORDER.get(str(finding.severity), 9),
            _KIND_ORDER.get(str(finding.kind), 9),
            finding.subject,
        ),
    )


def _as_text(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, float):
        return f"{value:.6f}".rstrip("0").rstrip(".")
    return str(value)


def _provenance_dict(provenance: Provenance | None) -> dict | None:
    if provenance is None:
        return None
    return {
        "page": provenance.page,
        "bbox": list(provenance.bbox) if provenance.bbox else None,
        "source_kind": str(provenance.source_kind),
        "table_index": provenance.table_index,
        "row": provenance.row,
        "column": provenance.column,
        "raw_text": provenance.raw_text,
        "label": provenance.label(),
    }


def _match_summary(result: ComparisonResult) -> dict:
    match = result.match
    if match is None:
        return {}
    return {
        "method": str(match.method),
        "pairs": len(match.pairs),
        "unmatched_a": match.unmatched_a,
        "unmatched_b": match.unmatched_b,
        "reversed_orientation": match.reversed_orientation,
        "rotation_offset": match.rotation_offset,
        "notes": match.notes,
        "systematic": (
            {
                "kind": str(match.systematic.kind),
                "magnitude": match.systematic.magnitude,
                "azimuth": match.systematic.azimuth_deg,
                "residual_rms": match.systematic.residual_rms_m,
                "message": match.systematic.message,
            }
            if match.systematic
            else None
        ),
        "recomputed_a": _recomputed_summary(result.recomputed_a),
        "recomputed_b": _recomputed_summary(result.recomputed_b),
    }


def _recomputed_summary(recomputed) -> dict | None:
    if recomputed is None:
        return None
    closure = recomputed.declared_closure
    return {
        "area_m2": recomputed.area_m2,
        "perimeter_grid_m": recomputed.perimeter_grid_m,
        "perimeter_ground_m": recomputed.perimeter_ground_m,
        "scale_factor": recomputed.scale_factor,
        "closure_linear_error": closure.linear_error if closure else None,
        "closure_precision": (
            None
            if closure is None
            else (
                None
                if closure.precision_denominator == float("inf")
                else closure.precision_denominator
            )
        ),
        "notes": recomputed.notes,
    }
