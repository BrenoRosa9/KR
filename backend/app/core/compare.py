"""Motor de comparação em quatro vias.

Para cada grandeza existem até quatro valores: o declarado em A, o declarado em
B, o recalculado a partir das coordenadas de A e o recalculado a partir das de
B. Cruzá-los nas combinações certas é o que separa dois achados de natureza
completamente diferente:

* declarado(A) vs recalculado(A) → **inconsistência interna** do documento A;
* recalculado(A) vs recalculado(B) → **divergência real** entre os documentos.

Comparar apenas declarado(A) contra declarado(B), que é o reflexo natural,
mistura os dois casos e produz um relatório que não diz onde está o problema.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from .angles import angular_difference, degrees_to_arcsec, to_dms_string
from .crs import CRSUndetermined, to_geographic
from .geodesy import (
    ClosureResult,
    combined_factor,
    grid_to_ground,
    interior_angles,
    polygon_area,
    segment_azimuths,
    segment_lengths,
    traverse_closure,
)
from .matching import MatchResult, match_vertices
from .numbers import format_br
from .schema import FieldKind, Measured, Parcel, Provenance, TextValue
from .tolerance import FLOAT_EPS, ToleranceProfile


class FindingKind(StrEnum):
    SYSTEMATIC = "systematic"
    INTER_DOCUMENT = "inter_document"
    INTERNAL = "internal"
    STRUCTURAL = "structural"
    LOW_CONFIDENCE = "low_confidence"
    DATA_GAP = "data_gap"


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass
class Finding:
    """Um achado do relatório, sempre rastreável até a origem."""

    kind: FindingKind
    severity: Severity
    field: FieldKind | None
    subject: str
    message: str
    value_a: float | str | None = None
    value_b: float | str | None = None
    delta: float | None = None
    tolerance: float | None = None
    unit: str = ""
    scope: str = "AB"
    provenance_a: Provenance | None = None
    provenance_b: Provenance | None = None


@dataclass
class Recomputed:
    """Grandezas derivadas exclusivamente das coordenadas dos vértices."""

    grid_distances: list[float] = field(default_factory=list)
    ground_distances: list[float] = field(default_factory=list)
    azimuths: list[float] = field(default_factory=list)
    interior_angles: list[float] = field(default_factory=list)
    area_m2: float | None = None
    perimeter_grid_m: float | None = None
    perimeter_ground_m: float | None = None
    scale_factor: float = 1.0
    declared_closure: ClosureResult | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class ComparisonResult:
    findings: list[Finding] = field(default_factory=list)
    match: MatchResult | None = None
    recomputed_a: Recomputed | None = None
    recomputed_b: Recomputed | None = None
    profile_name: str = ""
    summary: dict[str, int] = field(default_factory=dict)

    def by_kind(self, kind: FindingKind) -> list[Finding]:
        return [f for f in self.findings if f.kind == kind]

    @property
    def has_blocking(self) -> bool:
        return any(f.severity == Severity.ERROR for f in self.findings)


def recompute(parcel: Parcel, profile: ToleranceProfile) -> Recomputed:
    """Recalcula tudo o que as coordenadas permitem, mais o fechamento declarado."""
    result = Recomputed()

    if parcel.has_projected_ring:
        ring = parcel.ring()
        result.grid_distances = segment_lengths(ring)
        result.azimuths = segment_azimuths(ring)
        result.interior_angles = interior_angles(ring)
        result.area_m2 = polygon_area(ring)
        result.perimeter_grid_m = sum(result.grid_distances)
        result.scale_factor = _scale_factor(parcel, profile, result)
        result.ground_distances = [
            grid_to_ground(d, result.scale_factor) for d in result.grid_distances
        ]
        result.perimeter_ground_m = sum(result.ground_distances)
    else:
        result.notes.append(
            "Sem anel completo de coordenadas projetadas: recálculo geométrico "
            "não foi possível."
        )

    # O fechamento é percorrido no plano da projeção, então distâncias
    # declaradas como de terreno precisam voltar para a grade antes de somar.
    to_grid = result.scale_factor if parcel.distances_are_ground else 1.0
    declared = [
        (segment.azimuth.value, segment.distance.value * to_grid)
        for segment in parcel.segments
        if segment.azimuth is not None and segment.distance is not None
    ]
    if len(declared) == len(parcel.segments) and declared:
        start = (
            parcel.vertices[0].point() if parcel.vertices[0].has_projected else (0.0, 0.0)
        )
        result.declared_closure = traverse_closure(start, declared)

    return result


def _reference_distances(parcel: Parcel, recomputed: Recomputed) -> list[float]:
    """Recalculadas na mesma convenção em que o documento declara as suas."""
    return (
        recomputed.ground_distances
        if parcel.distances_are_ground
        else recomputed.grid_distances
    )


def _reference_perimeter(parcel: Parcel, recomputed: Recomputed) -> float | None:
    return (
        recomputed.perimeter_ground_m
        if parcel.distances_are_ground
        else recomputed.perimeter_grid_m
    )


def _scale_factor(
    parcel: Parcel, profile: ToleranceProfile, result: Recomputed
) -> float:
    """Fator combinado de escala e altitude no centroide do imóvel.

    Sem ele, distâncias recalculadas em UTM divergem sistematicamente das
    distâncias de campo do memorial em cerca de 0,4 m por quilômetro — o
    suficiente para reprovar todos os segmentos de um documento correto.
    """
    if not profile.apply_scale_factor:
        result.notes.append("Fator de escala desativado no perfil de tolerância.")
        return 1.0
    if parcel.crs is None or not parcel.crs.is_projected:
        result.notes.append(
            "CRS projetado desconhecido: distâncias comparadas como distância de "
            "grade, sem redução ao plano topográfico."
        )
        return 1.0

    ring = parcel.ring()
    mean_easting = sum(p[0] for p in ring) / len(ring)
    mean_northing = sum(p[1] for p in ring) / len(ring)
    try:
        _, latitude = to_geographic(parcel.crs, mean_easting, mean_northing)
    except CRSUndetermined as exc:  # pragma: no cover - CRS malformado
        result.notes.append(f"Fator de escala não aplicado: {exc}")
        return 1.0

    factor = combined_factor(
        mean_easting, latitude, height_m=parcel.average_height_m
    )
    result.notes.append(
        f"Fator combinado de escala {format_br(factor, 8)} aplicado "
        f"(altitude média {format_br(parcel.average_height_m, 0)} m)."
    )
    return factor


def compare_parcels(
    parcel_a: Parcel,
    parcel_b: Parcel,
    profile: ToleranceProfile,
) -> ComparisonResult:
    """Compara dois imóveis normalizados e devolve os achados."""
    result = ComparisonResult(profile_name=profile.name)
    result.recomputed_a = recompute(parcel_a, profile)
    result.recomputed_b = recompute(parcel_b, profile)

    _check_crs(parcel_a, parcel_b, result)
    _check_internal(parcel_a, result.recomputed_a, profile, "A", result)
    _check_internal(parcel_b, result.recomputed_b, profile, "B", result)

    points_a = parcel_a.ring() if parcel_a.has_projected_ring else None
    points_b = parcel_b.ring() if parcel_b.has_projected_ring else None
    match = match_vertices(
        [v.code for v in parcel_a.vertices],
        [v.code for v in parcel_b.vertices],
        points_a,
        points_b,
    )
    result.match = match
    for note in match.notes:
        result.findings.append(
            Finding(
                kind=FindingKind.STRUCTURAL,
                severity=Severity.INFO,
                field=None,
                subject="correspondência",
                message=note,
            )
        )

    _check_structure(parcel_a, parcel_b, match, result)
    if match.systematic is not None:
        _report_systematic(match, result)

    _check_coordinates(parcel_a, parcel_b, match, profile, result)
    _check_segments(parcel_a, parcel_b, match, profile, result)
    _check_totals(parcel_a, parcel_b, profile, result)
    _check_text_fields(parcel_a, parcel_b, result)
    _check_tax_ids(parcel_a, parcel_b, result)
    _check_text_magnitude_citations(parcel_a, result.recomputed_a, profile, "A", result)
    _check_text_magnitude_citations(parcel_b, result.recomputed_b, profile, "B", result)
    _check_confidence(parcel_a, profile, "A", result)
    _check_confidence(parcel_b, profile, "B", result)

    result.summary = _summarize(result)
    return result


def _check_crs(parcel_a: Parcel, parcel_b: Parcel, result: ComparisonResult) -> None:
    """CRS ausente ou divergente é bloqueio, não observação."""
    for parcel, scope in ((parcel_a, "A"), (parcel_b, "B")):
        if parcel.crs is None:
            result.findings.append(
                Finding(
                    kind=FindingKind.DATA_GAP,
                    severity=Severity.ERROR,
                    field=FieldKind.DATUM,
                    subject=f"documento {scope}",
                    message=(
                        "Datum e/ou fuso não identificados no documento. É preciso "
                        "confirmação humana antes de qualquer comparação — assumir um "
                        "default produziria laudo inválido com aparência de válido."
                    ),
                    scope=scope,
                )
            )

    if parcel_a.crs is None or parcel_b.crs is None:
        return

    if parcel_a.crs.epsg != parcel_b.crs.epsg:
        result.findings.append(
            Finding(
                kind=FindingKind.INTER_DOCUMENT,
                severity=Severity.WARNING,
                field=FieldKind.DATUM,
                subject="sistema de referência",
                message=(
                    f"Documentos em sistemas diferentes: A em {parcel_a.crs.describe()} "
                    f"e B em {parcel_b.crs.describe()}. As coordenadas foram levadas ao "
                    "sistema de A antes da comparação; verifique se a conversão é "
                    "aceitável para a finalidade do laudo."
                ),
                value_a=parcel_a.crs.describe(),
                value_b=parcel_b.crs.describe(),
            )
        )


def _check_arc(
    segment,
    computed_chord: float,
    label: str,
    profile: ToleranceProfile,
    scope: str,
    result: ComparisonResult,
) -> None:
    """Confere um lado curvo pelo que de fato o descreve.

    A distância entre os dois vértices é a corda; o documento publica o
    desenvolvimento. Com o ângulo central declarado, as duas grandezas são
    previsíveis a partir do raio, e é essa previsão que vale como conferência.
    Sem o ângulo central não há o que verificar, e o lado é apenas registrado
    como curva — o silêncio aqui é honesto, ao contrário de uma reprovação que
    só existiria por comparar corda com arco.
    """
    radius = segment.arc_radius
    development = segment.arc_development
    if radius is None or development is None:
        return

    expected_chord = segment.expected_chord()
    if expected_chord is None:
        result.findings.append(
            Finding(
                kind=FindingKind.STRUCTURAL,
                severity=Severity.INFO,
                field=FieldKind.DISTANCE,
                subject=label,
                message=(
                    f"Documento {scope}: lado curvo, raio {format_br(radius.value, 3)} m "
                    f"e desenvolvimento {format_br(development.value, 3)} m. Sem o "
                    "ângulo central declarado não há como conferir a curva; a "
                    f"distância entre os vértices é {format_br(computed_chord, 3)} m."
                ),
                value_a=development.value,
                unit="m",
                scope=scope,
                provenance_a=development.provenance,
            )
        )
        return

    tolerance = profile.for_distance(expected_chord, radius.halfwidth)
    delta = computed_chord - expected_chord
    if profile.exceeds(delta, tolerance):
        result.findings.append(
            Finding(
                kind=FindingKind.INTERNAL,
                severity=Severity.WARNING,
                field=FieldKind.DISTANCE,
                subject=label,
                message=(
                    f"Documento {scope}: lado curvo inconsistente. Raio "
                    f"{format_br(radius.value, 3)} m e ângulo central "
                    f"{format_br(segment.central_angle.value, 4)}° dão uma corda de "
                    f"{format_br(expected_chord, 3)} m, mas as coordenadas dos "
                    f"vértices distam {format_br(computed_chord, 3)} m."
                ),
                value_a=expected_chord,
                value_b=computed_chord,
                delta=delta,
                tolerance=tolerance,
                unit="m",
                scope=scope,
                provenance_a=radius.provenance,
            )
        )

    expected_development = segment.expected_development()
    if expected_development is None:
        return

    tolerance = profile.for_distance(expected_development, development.halfwidth)
    delta = development.value - expected_development
    if profile.exceeds(delta, tolerance):
        result.findings.append(
            Finding(
                kind=FindingKind.INTERNAL,
                severity=Severity.WARNING,
                field=FieldKind.DISTANCE,
                subject=label,
                message=(
                    f"Documento {scope}: desenvolvimento declarado "
                    f"{format_br(development.value, 3)} m não corresponde ao arco de "
                    f"raio {format_br(radius.value, 3)} m com ângulo central "
                    f"{format_br(segment.central_angle.value, 4)}°, que mede "
                    f"{format_br(expected_development, 3)} m."
                ),
                value_a=development.value,
                value_b=expected_development,
                delta=delta,
                tolerance=tolerance,
                unit="m",
                scope=scope,
                provenance_a=development.provenance,
            )
        )


def _check_internal(
    parcel: Parcel,
    recomputed: Recomputed,
    profile: ToleranceProfile,
    scope: str,
    result: ComparisonResult,
) -> None:
    """Confronta os valores declarados com os recalculados do mesmo documento."""
    reference = _reference_distances(parcel, recomputed)

    for index, segment in enumerate(parcel.segments):
        label = parcel.segment_label(segment)

        if segment.is_arc and index < len(reference):
            _check_arc(segment, reference[index], label, profile, scope, result)

        if segment.distance is not None and index < len(reference):
            declared = segment.distance.value
            computed = reference[index]
            tolerance = profile.for_distance(declared, segment.distance.halfwidth)
            delta = declared - computed
            if profile.exceeds(delta, tolerance):
                result.findings.append(
                    Finding(
                        kind=FindingKind.INTERNAL,
                        severity=Severity.WARNING,
                        field=FieldKind.DISTANCE,
                        subject=label,
                        message=(
                            f"Documento {scope}: distância declarada "
                            f"{format_br(declared, 3)} m difere da recalculada a partir "
                            f"das próprias coordenadas ({format_br(computed, 3)} m). O "
                            "documento é inconsistente consigo mesmo."
                        ),
                        value_a=declared,
                        value_b=computed,
                        delta=delta,
                        tolerance=tolerance,
                        unit="m",
                        scope=scope,
                        provenance_a=segment.distance.provenance,
                    )
                )

        if segment.azimuth is not None and index < len(recomputed.azimuths):
            declared = segment.azimuth.value
            computed = recomputed.azimuths[index]
            tolerance = profile.for_azimuth(segment.azimuth.halfwidth)
            delta = angular_difference(declared, computed)
            if profile.exceeds(delta, tolerance):
                result.findings.append(
                    Finding(
                        kind=FindingKind.INTERNAL,
                        severity=Severity.WARNING,
                        field=FieldKind.AZIMUTH,
                        subject=label,
                        message=(
                            f"Documento {scope}: azimute declarado "
                            f"{to_dms_string(declared)} difere do recalculado "
                            f"{to_dms_string(computed)} em "
                            f"{format_br(degrees_to_arcsec(abs(delta)), 0)}\"."
                        ),
                        value_a=declared,
                        value_b=computed,
                        delta=delta,
                        tolerance=tolerance,
                        unit="°",
                        scope=scope,
                        provenance_a=segment.azimuth.provenance,
                    )
                )

    _check_internal_totals(parcel, recomputed, profile, scope, result)
    _check_closure(parcel, recomputed, profile, scope, result)


def _check_internal_totals(
    parcel: Parcel,
    recomputed: Recomputed,
    profile: ToleranceProfile,
    scope: str,
    result: ComparisonResult,
) -> None:
    if parcel.area is not None and recomputed.area_m2 is not None:
        declared, computed = parcel.area.value, recomputed.area_m2
        tolerance = profile.for_area(declared, parcel.area.halfwidth)
        delta = declared - computed
        if profile.exceeds(delta, tolerance):
            result.findings.append(
                Finding(
                    kind=FindingKind.INTERNAL,
                    severity=Severity.ERROR,
                    field=FieldKind.AREA,
                    subject=f"área do documento {scope}",
                    message=(
                        f"Documento {scope}: área declarada {format_br(declared, 2)} m² "
                        f"não confere com a área calculada pelos vértices "
                        f"({format_br(computed, 2)} m²). Diferença de "
                        f"{format_br(abs(delta), 2)} m²."
                    ),
                    value_a=declared,
                    value_b=computed,
                    delta=delta,
                    tolerance=tolerance,
                    unit="m²",
                    scope=scope,
                    provenance_a=parcel.area.provenance,
                )
            )

    reference_perimeter = _reference_perimeter(parcel, recomputed)
    if parcel.perimeter is not None and reference_perimeter is not None:
        declared, computed = parcel.perimeter.value, reference_perimeter
        hw = parcel.perimeter.halfwidth
        # Acúmulo de arredondamento ao longo dos lados.
        tolerance = max(
            profile.for_perimeter(hw),
            len(parcel.segments) * hw if hw > 0 else FLOAT_EPS,
        )
        delta = declared - computed
        if profile.exceeds(delta, tolerance):
            result.findings.append(
                Finding(
                    kind=FindingKind.INTERNAL,
                    severity=Severity.WARNING,
                    field=FieldKind.PERIMETER,
                    subject=f"perímetro do documento {scope}",
                    message=(
                        f"Documento {scope}: perímetro declarado "
                        f"{format_br(declared, 3)} m difere do recalculado "
                        f"({format_br(computed, 3)} m)."
                    ),
                    value_a=declared,
                    value_b=computed,
                    delta=delta,
                    tolerance=tolerance,
                    unit="m",
                    scope=scope,
                    provenance_a=parcel.perimeter.provenance,
                )
            )

    if recomputed.interior_angles:
        total = sum(recomputed.interior_angles)
        expected = (len(recomputed.interior_angles) - 2) * 180.0
        tolerance = profile.for_angle() * len(recomputed.interior_angles)
        if profile.exceeds(total - expected, tolerance):
            result.findings.append(
                Finding(
                    kind=FindingKind.INTERNAL,
                    severity=Severity.WARNING,
                    field=FieldKind.INTERIOR_ANGLE,
                    subject=f"soma dos ângulos internos ({scope})",
                    message=(
                        f"Documento {scope}: soma dos ângulos internos é "
                        f"{format_br(total, 6)}°, esperado {format_br(expected, 0)}°. "
                        "Indica polígono não "
                        "simples, vértice fora de ordem ou coordenada errada."
                    ),
                    value_a=total,
                    value_b=expected,
                    delta=total - expected,
                    tolerance=tolerance,
                    unit="°",
                    scope=scope,
                )
            )


def _check_closure(
    parcel: Parcel,
    recomputed: Recomputed,
    profile: ToleranceProfile,
    scope: str,
    result: ComparisonResult,
) -> None:
    closure = recomputed.declared_closure
    if closure is None:
        return

    precision = closure.precision_denominator
    # O fechamento de uma poligonal com distâncias arredondadas nunca é zero
    # absoluto. O orçamento é a soma das meias-larguras das distâncias
    # declaradas — qualquer erro além disso não se explica por arredondamento.
    halfwidths = [
        segment.distance.halfwidth
        for segment in parcel.segments
        if segment.distance is not None and segment.distance.halfwidth > 0
    ]
    budget = sum(halfwidths) if halfwidths else FLOAT_EPS

    if profile.exceeds(closure.linear_error, budget):
        result.findings.append(
            Finding(
                kind=FindingKind.INTERNAL,
                severity=Severity.ERROR,
                field=None,
                subject=f"fechamento da poligonal ({scope})",
                message=(
                    f"Documento {scope}: percorrendo os azimutes e distâncias "
                    f"declarados, a poligonal não fecha. Erro linear de "
                    f"{format_br(closure.linear_error, 3)} m em "
                    f"{format_br(closure.total_length, 2)} m percorridos"
                    + (
                        f", precisão 1:{format_br(precision, 0)}."
                        if precision != float("inf")
                        else "."
                    )
                ),
                value_a=closure.linear_error,
                tolerance=budget,
                unit="m",
                scope=scope,
            )
        )
    else:
        result.findings.append(
            Finding(
                kind=FindingKind.INTERNAL,
                severity=Severity.INFO,
                field=None,
                subject=f"fechamento da poligonal ({scope})",
                message=(
                    f"Documento {scope}: fechamento consistente, erro linear de "
                    f"{format_br(closure.linear_error, 4)} m "
                    f"(1:{format_br(precision, 0)})."
                ),
                value_a=closure.linear_error,
                unit="m",
                scope=scope,
            )
        )


def _report_systematic(match: MatchResult, result: ComparisonResult) -> None:
    systematic = match.systematic
    assert systematic is not None
    result.findings.append(
        Finding(
            kind=FindingKind.SYSTEMATIC,
            severity=Severity.ERROR,
            field=None,
            subject=f"padrão global ({systematic.kind})",
            message=systematic.message,
            value_a=systematic.magnitude,
            delta=systematic.residual_rms_m,
            unit="m" if systematic.kind == "translation" else "",
        )
    )


def _check_structure(
    parcel_a: Parcel,
    parcel_b: Parcel,
    match: MatchResult,
    result: ComparisonResult,
) -> None:
    if len(parcel_a.vertices) != len(parcel_b.vertices):
        result.findings.append(
            Finding(
                kind=FindingKind.STRUCTURAL,
                severity=Severity.ERROR,
                field=None,
                subject="quantidade de vértices",
                message=(
                    f"Documento A descreve {len(parcel_a.vertices)} vértices e o "
                    f"documento B, {len(parcel_b.vertices)}. A geometria descrita não "
                    "é a mesma."
                ),
                value_a=len(parcel_a.vertices),
                value_b=len(parcel_b.vertices),
            )
        )

    for index in match.unmatched_a:
        result.findings.append(
            Finding(
                kind=FindingKind.STRUCTURAL,
                severity=Severity.WARNING,
                field=FieldKind.VERTEX_CODE,
                subject=parcel_a.vertices[index].code,
                message=(
                    f"Vértice {parcel_a.vertices[index].code} do documento A não tem "
                    "correspondente no documento B."
                ),
                scope="A",
                provenance_a=parcel_a.vertices[index].code_provenance,
            )
        )
    for index in match.unmatched_b:
        result.findings.append(
            Finding(
                kind=FindingKind.STRUCTURAL,
                severity=Severity.WARNING,
                field=FieldKind.VERTEX_CODE,
                subject=parcel_b.vertices[index].code,
                message=(
                    f"Vértice {parcel_b.vertices[index].code} do documento B não tem "
                    "correspondente no documento A."
                ),
                scope="B",
                provenance_b=parcel_b.vertices[index].code_provenance,
            )
        )


def _check_coordinates(
    parcel_a: Parcel,
    parcel_b: Parcel,
    match: MatchResult,
    profile: ToleranceProfile,
    result: ComparisonResult,
) -> None:
    """Compara coordenadas vértice a vértice.

    Quando um padrão sistemático foi detectado, os desvios individuais viram
    informação em vez de erro: já foram explicados por um único achado, e
    repetir a mesma divergência N vezes só esconde os problemas reais.
    """
    systematic = match.systematic is not None

    for pair in match.pairs:
        vertex_a = parcel_a.vertices[pair.index_a]
        vertex_b = parcel_b.vertices[pair.index_b]
        if not (vertex_a.has_projected and vertex_b.has_projected):
            continue

        tolerance = profile.for_coordinate(
            vertex_a.coordinate_halfwidth(), vertex_b.coordinate_halfwidth()
        )
        for field_kind, measured_a, measured_b in (
            (FieldKind.EASTING, vertex_a.easting, vertex_b.easting),
            (FieldKind.NORTHING, vertex_a.northing, vertex_b.northing),
        ):
            assert measured_a is not None and measured_b is not None
            delta = measured_a.value - measured_b.value
            if not profile.exceeds(delta, tolerance):
                continue
            result.findings.append(
                Finding(
                    kind=(
                        FindingKind.SYSTEMATIC
                        if systematic
                        else FindingKind.INTER_DOCUMENT
                    ),
                    severity=Severity.INFO if systematic else Severity.ERROR,
                    field=field_kind,
                    subject=f"{vertex_a.code} / {vertex_b.code}",
                    message=(
                        f"{field_kind.value} difere em {format_br(abs(delta), 3)} m "
                        f"(A: {format_br(measured_a.value, 3)}; "
                        f"B: {format_br(measured_b.value, 3)})"
                        + (
                            " — coerente com o padrão sistemático já reportado."
                            if systematic
                            else "."
                        )
                    ),
                    value_a=measured_a.value,
                    value_b=measured_b.value,
                    delta=delta,
                    tolerance=tolerance,
                    unit="m",
                    provenance_a=measured_a.provenance,
                    provenance_b=measured_b.provenance,
                )
            )


def _check_segments(
    parcel_a: Parcel,
    parcel_b: Parcel,
    match: MatchResult,
    profile: ToleranceProfile,
    result: ComparisonResult,
) -> None:
    """Compara segmentos correspondentes, tanto recalculados quanto declarados."""
    mapping = {pair.index_a: pair.index_b for pair in match.pairs}
    recomputed_a = result.recomputed_a
    recomputed_b = result.recomputed_b
    if recomputed_a is None or recomputed_b is None:
        return

    segments_b = {
        (segment.from_index, segment.to_index): index
        for index, segment in enumerate(parcel_b.segments)
    }

    for index_a, segment_a in enumerate(parcel_a.segments):
        from_b = mapping.get(segment_a.from_index)
        to_b = mapping.get(segment_a.to_index)
        if from_b is None or to_b is None:
            continue
        index_b = segments_b.get((from_b, to_b))
        if index_b is None:
            continue

        label = (
            f"{parcel_a.segment_label(segment_a)} / "
            f"{parcel_b.segment_label(parcel_b.segments[index_b])}"
        )

        if index_a < len(recomputed_a.ground_distances) and index_b < len(
            recomputed_b.ground_distances
        ):
            value_a = recomputed_a.ground_distances[index_a]
            value_b = recomputed_b.ground_distances[index_b]
            tolerance = profile.for_distance(value_a)
            delta = value_a - value_b
            if profile.exceeds(delta, tolerance):
                result.findings.append(
                    Finding(
                        kind=FindingKind.INTER_DOCUMENT,
                        severity=Severity.ERROR,
                        field=FieldKind.DISTANCE,
                        subject=label,
                        message=(
                            "Distância recalculada pelas coordenadas divergindo entre os "
                            f"documentos: {format_br(value_a, 3)} m em A contra "
                            f"{format_br(value_b, 3)} m em B."
                        ),
                        value_a=value_a,
                        value_b=value_b,
                        delta=delta,
                        tolerance=tolerance,
                        unit="m",
                    )
                )

        segment_b = parcel_b.segments[index_b]
        _compare_declared(
            segment_a, segment_b, label, profile, result
        )


def _compare_declared(
    segment_a,
    segment_b,
    label: str,
    profile: ToleranceProfile,
    result: ComparisonResult,
) -> None:
    if segment_a.distance is not None and segment_b.distance is not None:
        tolerance = profile.for_distance(
            segment_a.distance.value,
            segment_a.distance.halfwidth,
            segment_b.distance.halfwidth,
        )
        delta = segment_a.distance.value - segment_b.distance.value
        if profile.exceeds(delta, tolerance):
            result.findings.append(
                Finding(
                    kind=FindingKind.INTER_DOCUMENT,
                    severity=Severity.ERROR,
                    field=FieldKind.DISTANCE,
                    subject=label,
                    message=(
                        "Distância declarada divergindo entre os documentos: "
                        f"{format_br(segment_a.distance.value, 3)} m em A contra "
                        f"{format_br(segment_b.distance.value, 3)} m em B."
                    ),
                    value_a=segment_a.distance.value,
                    value_b=segment_b.distance.value,
                    delta=delta,
                    tolerance=tolerance,
                    unit="m",
                    provenance_a=segment_a.distance.provenance,
                    provenance_b=segment_b.distance.provenance,
                )
            )

    if segment_a.azimuth is not None and segment_b.azimuth is not None:
        tolerance = profile.for_azimuth(
            segment_a.azimuth.halfwidth, segment_b.azimuth.halfwidth
        )
        delta = angular_difference(segment_a.azimuth.value, segment_b.azimuth.value)
        if profile.exceeds(delta, tolerance):
            result.findings.append(
                Finding(
                    kind=FindingKind.INTER_DOCUMENT,
                    severity=Severity.ERROR,
                    field=FieldKind.AZIMUTH,
                    subject=label,
                    message=(
                        "Azimute declarado divergindo entre os documentos: "
                        f"{to_dms_string(segment_a.azimuth.value)} em A contra "
                        f"{to_dms_string(segment_b.azimuth.value)} em B "
                        f"({format_br(degrees_to_arcsec(abs(delta)), 0)}\" de "
                        "diferença)."
                    ),
                    value_a=segment_a.azimuth.value,
                    value_b=segment_b.azimuth.value,
                    delta=delta,
                    tolerance=tolerance,
                    unit="°",
                    provenance_a=segment_a.azimuth.provenance,
                    provenance_b=segment_b.azimuth.provenance,
                )
            )

    if (
        segment_a.confrontant is not None
        and segment_b.confrontant is not None
        and _normalize_text(segment_a.confrontant.value)
        != _normalize_text(segment_b.confrontant.value)
    ):
        result.findings.append(
            Finding(
                kind=FindingKind.INTER_DOCUMENT,
                severity=Severity.WARNING,
                field=FieldKind.CONFRONTANT,
                subject=label,
                message=(
                    f"Confrontante diferente: “{segment_a.confrontant.value}” em A "
                    f"contra “{segment_b.confrontant.value}” em B."
                ),
                value_a=segment_a.confrontant.value,
                value_b=segment_b.confrontant.value,
                provenance_a=segment_a.confrontant.provenance,
                provenance_b=segment_b.confrontant.provenance,
            )
        )


def _check_totals(
    parcel_a: Parcel,
    parcel_b: Parcel,
    profile: ToleranceProfile,
    result: ComparisonResult,
) -> None:
    recomputed_a = result.recomputed_a
    recomputed_b = result.recomputed_b
    if recomputed_a is None or recomputed_b is None:
        return

    if recomputed_a.area_m2 is not None and recomputed_b.area_m2 is not None:
        tolerance = profile.for_area(recomputed_a.area_m2)
        delta = recomputed_a.area_m2 - recomputed_b.area_m2
        if profile.exceeds(delta, tolerance):
            result.findings.append(
                Finding(
                    kind=FindingKind.INTER_DOCUMENT,
                    severity=Severity.ERROR,
                    field=FieldKind.AREA,
                    subject="área calculada",
                    message=(
                        "Área calculada pelos vértices divergindo entre os documentos: "
                        f"{format_br(recomputed_a.area_m2, 2)} m² em A contra "
                        f"{format_br(recomputed_b.area_m2, 2)} m² em B "
                        f"(diferença de {format_br(abs(delta), 2)} m²)."
                    ),
                    value_a=recomputed_a.area_m2,
                    value_b=recomputed_b.area_m2,
                    delta=delta,
                    tolerance=tolerance,
                    unit="m²",
                )
            )

    _compare_measured(
        parcel_a.area,
        parcel_b.area,
        FieldKind.AREA,
        "área declarada",
        lambda a, b: profile.for_area(a.value, a.halfwidth, b.halfwidth),
        "m²",
        result,
    )
    _compare_measured(
        parcel_a.perimeter,
        parcel_b.perimeter,
        FieldKind.PERIMETER,
        "perímetro declarado",
        lambda a, b: profile.for_perimeter(a.halfwidth, b.halfwidth),
        "m",
        result,
    )


def _compare_measured(
    measured_a: Measured | None,
    measured_b: Measured | None,
    field_kind: FieldKind,
    subject: str,
    tolerance_fn,
    unit: str,
    result: ComparisonResult,
) -> None:
    if measured_a is None or measured_b is None:
        return
    tolerance = tolerance_fn(measured_a, measured_b)
    delta = measured_a.value - measured_b.value
    if abs(delta) <= max(tolerance, FLOAT_EPS):
        return
    result.findings.append(
        Finding(
            kind=FindingKind.INTER_DOCUMENT,
            severity=Severity.ERROR,
            field=field_kind,
            subject=subject,
            message=(
                f"{subject.capitalize()} difere entre os documentos: "
                f"{format_br(measured_a.value, 3)} {unit} em A contra "
                f"{format_br(measured_b.value, 3)} {unit} em B."
            ),
            value_a=measured_a.value,
            value_b=measured_b.value,
            delta=delta,
            tolerance=tolerance,
            unit=unit,
            provenance_a=measured_a.provenance,
            provenance_b=measured_b.provenance,
        )
    )


def _check_text_fields(
    parcel_a: Parcel, parcel_b: Parcel, result: ComparisonResult
) -> None:
    if parcel_a.matricula is None or parcel_b.matricula is None:
        return
    if _normalize_text(parcel_a.matricula.value) == _normalize_text(
        parcel_b.matricula.value
    ):
        return
    result.findings.append(
        Finding(
            kind=FindingKind.INTER_DOCUMENT,
            severity=Severity.WARNING,
            field=FieldKind.MATRICULA,
            subject="matrícula",
            message=(
                f"Matrícula diferente entre os documentos: “{parcel_a.matricula.value}” "
                f"em A contra “{parcel_b.matricula.value}” em B."
            ),
            value_a=parcel_a.matricula.value,
            value_b=parcel_b.matricula.value,
            provenance_a=parcel_a.matricula.provenance,
            provenance_b=parcel_b.matricula.provenance,
        )
    )


def _check_tax_ids(
    parcel_a: Parcel, parcel_b: Parcel, result: ComparisonResult
) -> None:
    """Compara o conjunto de CPF/CNPJ citados em cada documento."""

    def fmt(digits: str) -> str:
        if len(digits) == 11:
            return f"{digits[:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:]}"
        if len(digits) == 14:
            return (
                f"{digits[:2]}.{digits[2:5]}.{digits[5:8]}/"
                f"{digits[8:12]}-{digits[12:]}"
            )
        return digits

    ids_a = _tax_id_index(parcel_a)
    ids_b = _tax_id_index(parcel_b)
    if not ids_a and not ids_b:
        return

    only_a = sorted(set(ids_a) - set(ids_b))
    only_b = sorted(set(ids_b) - set(ids_a))
    shared = sorted(set(ids_a) & set(ids_b))

    if shared and not only_a and not only_b:
        result.findings.append(
            Finding(
                kind=FindingKind.INTER_DOCUMENT,
                severity=Severity.INFO,
                field=FieldKind.CPF,
                subject="CPF/CNPJ",
                message=(
                    f"Os {len(shared)} CPF/CNPJ citados coincidem entre os documentos."
                ),
                value_a=len(shared),
                value_b=len(shared),
            )
        )
        return

    for digits in only_a:
        hit = ids_a[digits]
        kind = FieldKind.CNPJ if len(digits) == 14 else FieldKind.CPF
        result.findings.append(
            Finding(
                kind=FindingKind.INTER_DOCUMENT,
                severity=Severity.ERROR,
                field=kind,
                subject=fmt(digits),
                message=(
                    f"{kind.value.upper()} {fmt(digits)} aparece no "
                    f"documento A (p. {hit.provenance.page if hit.provenance else '?'}) "
                    "e não no documento B."
                ),
                value_a=fmt(digits),
                provenance_a=hit.provenance,
            )
        )

    for digits in only_b:
        hit = ids_b[digits]
        kind = FieldKind.CNPJ if len(digits) == 14 else FieldKind.CPF
        result.findings.append(
            Finding(
                kind=FindingKind.INTER_DOCUMENT,
                severity=Severity.ERROR,
                field=kind,
                subject=fmt(digits),
                message=(
                    f"{kind.value.upper()} {fmt(digits)} aparece no "
                    f"documento B (p. {hit.provenance.page if hit.provenance else '?'}) "
                    "e não no documento A."
                ),
                value_b=fmt(digits),
                provenance_b=hit.provenance,
            )
        )

    for digits, hit in ids_a.items():
        if hit.confidence < 0.8:
            kind = FieldKind.CNPJ if len(digits) == 14 else FieldKind.CPF
            result.findings.append(
                Finding(
                    kind=FindingKind.LOW_CONFIDENCE,
                    severity=Severity.WARNING,
                    field=kind,
                    subject=fmt(digits),
                    message=(
                        f"Documento A: {kind.value.upper()} {fmt(digits)} "
                        "não passou na verificação dos dígitos — confira a leitura."
                    ),
                    value_a=fmt(digits),
                    provenance_a=hit.provenance,
                    scope="A",
                )
            )


def _tax_id_index(parcel: Parcel) -> dict[str, TextValue]:
    """Última ocorrência de cada CPF/CNPJ (dígitos → TextValue)."""
    index: dict[str, TextValue] = {}
    for item in parcel.tax_ids:
        digits = "".join(ch for ch in item.value if ch.isdigit())
        if len(digits) in {11, 14}:
            index[digits] = item
    return index


def _check_text_magnitude_citations(
    parcel: Parcel,
    recomputed: Recomputed,
    profile: ToleranceProfile,
    scope: str,
    result: ComparisonResult,
) -> None:
    """Confronta citações de área/perímetro no texto com a tabela (recalculada).

    O memorial costuma declarar a área na capa/descrição e, ao mesmo tempo,
    listar áreas de ruas, lotes vizinhos ou o núcleo inteiro. Cada citação
    distinta é confrontada com a área calculada pelos vértices da tabela: a
    que bate é coerente; as outras são apontadas como divergência texto × tabela.
    """
    if recomputed.area_m2 is not None:
        citations = parcel.area_citations or (
            [parcel.area] if parcel.area is not None else []
        )
        matching = [
            c
            for c in citations
            if c is not None and not profile.values_differ(c.value, recomputed.area_m2, c.halfwidth)
        ]
        conflicting = [
            c
            for c in citations
            if c is not None and profile.values_differ(c.value, recomputed.area_m2, c.halfwidth)
        ]
        # Só reporta conflito texto×tabela quando existe pelo menos uma citação
        # que bate e outra que não — senão o achado interno já coberto por
        # área declarada × recalculada basta.
        if matching and conflicting:
            for citation in conflicting:
                page = citation.provenance.page if citation.provenance else "?"
                result.findings.append(
                    Finding(
                        kind=FindingKind.INTERNAL,
                        severity=Severity.WARNING,
                        field=FieldKind.AREA,
                        subject=f"área no texto (p. {page})",
                        message=(
                            f"Documento {scope}: o texto na página {page} declara "
                            f"área {format_br(citation.value, 2)} m², que não confere "
                            f"com a área calculada pela tabela de vértices "
                            f"({format_br(recomputed.area_m2, 2)} m²). Pode ser área "
                            "de outra parcela, rua ou núcleo citada no mesmo documento."
                        ),
                        value_a=citation.value,
                        value_b=recomputed.area_m2,
                        delta=citation.value - recomputed.area_m2,
                        unit="m²",
                        scope=scope,
                        provenance_a=citation.provenance,
                    )
                )
        elif len(citations) > 1:
            # Várias áreas no texto, nenhuma bate com a tabela — aponta o par
            # texto×texto para revisão.
            primary = citations[0]
            for other in citations[1:]:
                if not profile.values_differ(primary.value, other.value, primary.halfwidth, other.halfwidth):
                    continue
                result.findings.append(
                    Finding(
                        kind=FindingKind.INTERNAL,
                        severity=Severity.WARNING,
                        field=FieldKind.AREA,
                        subject=f"áreas no texto ({scope})",
                        message=(
                            f"Documento {scope}: o texto declara áreas distintas — "
                            f"{format_br(primary.value, 2)} m² "
                            f"(p. {primary.provenance.page if primary.provenance else '?'}) "
                            f"e {format_br(other.value, 2)} m² "
                            f"(p. {other.provenance.page if other.provenance else '?'})."
                        ),
                        value_a=primary.value,
                        value_b=other.value,
                        delta=primary.value - other.value,
                        unit="m²",
                        scope=scope,
                        provenance_a=primary.provenance,
                        provenance_b=other.provenance,
                    )
                )

    if recomputed.perimeter_ground_m is not None or recomputed.perimeter_grid_m is not None:
        reference = (
            recomputed.perimeter_ground_m
            if parcel.distances_are_ground
            else recomputed.perimeter_grid_m
        )
        if reference is None:
            return
        citations = parcel.perimeter_citations or (
            [parcel.perimeter] if parcel.perimeter is not None else []
        )
        matching = [
            c
            for c in citations
            if c is not None and not profile.values_differ(c.value, reference, c.halfwidth)
        ]
        conflicting = [
            c
            for c in citations
            if c is not None and profile.values_differ(c.value, reference, c.halfwidth)
        ]
        if matching and conflicting:
            for citation in conflicting:
                page = citation.provenance.page if citation.provenance else "?"
                result.findings.append(
                    Finding(
                        kind=FindingKind.INTERNAL,
                        severity=Severity.WARNING,
                        field=FieldKind.PERIMETER,
                        subject=f"perímetro no texto (p. {page})",
                        message=(
                            f"Documento {scope}: o texto na página {page} declara "
                            f"perímetro {format_br(citation.value, 2)} m, que não confere "
                            f"com o perímetro calculado pela tabela "
                            f"({format_br(reference, 2)} m)."
                        ),
                        value_a=citation.value,
                        value_b=reference,
                        delta=citation.value - reference,
                        unit="m",
                        scope=scope,
                        provenance_a=citation.provenance,
                    )
                )


def _check_confidence(
    parcel: Parcel, profile: ToleranceProfile, scope: str, result: ComparisonResult
) -> None:
    """Registra valores de baixa confiança para a fila de revisão humana."""
    for vertex in parcel.vertices:
        for field_kind, measured in (
            (FieldKind.EASTING, vertex.easting),
            (FieldKind.NORTHING, vertex.northing),
            (FieldKind.LATITUDE, vertex.latitude),
            (FieldKind.LONGITUDE, vertex.longitude),
        ):
            if measured is None or measured.edited:
                continue
            if measured.confidence >= profile.low_confidence:
                continue
            result.findings.append(
                Finding(
                    kind=FindingKind.LOW_CONFIDENCE,
                    severity=Severity.WARNING,
                    field=field_kind,
                    subject=f"{vertex.code} ({scope})",
                    message=(
                        f"Valor extraído com confiança {measured.confidence:.0%} — "
                        f"lido como “{_raw_text(measured)}”. Requer conferência "
                        "humana antes de constar no laudo."
                    ),
                    value_a=measured.value,
                    scope=scope,
                    provenance_a=measured.provenance,
                )
            )

    # Os totais do documento não vêm de tabela: são garimpados no texto, onde a
    # disputa entre candidatos é comum. Se a extração já saiu insegura, o laudo
    # tem de dizer isso em vez de deixar o número passar como se fosse certo.
    for field_kind, value in (
        (FieldKind.AREA, parcel.area),
        (FieldKind.PERIMETER, parcel.perimeter),
        (FieldKind.MATRICULA, parcel.matricula),
    ):
        if value is None or value.edited:
            continue
        if value.confidence >= profile.low_confidence:
            continue
        raw = value.provenance.raw_text if value.provenance else ""
        result.findings.append(
            Finding(
                kind=FindingKind.LOW_CONFIDENCE,
                severity=Severity.WARNING,
                field=field_kind,
                subject=f"{field_kind} ({scope})",
                message=(
                    f"Valor extraído com confiança {value.confidence:.0%} — lido de "
                    f"“{raw}”. Requer conferência humana antes de constar no laudo."
                ),
                value_a=value.value,
                scope=scope,
                provenance_a=value.provenance,
            )
        )


def _normalize_text(text: str) -> str:
    return " ".join(text.lower().split())


def _raw_text(measured: Measured) -> str:
    """Trecho original de onde o valor saiu, para o revisor conferir."""
    return measured.provenance.raw_text if measured.provenance else ""


def _summarize(result: ComparisonResult) -> dict[str, int]:
    summary = {
        "total": len(result.findings),
        "erros": sum(1 for f in result.findings if f.severity == Severity.ERROR),
        "avisos": sum(1 for f in result.findings if f.severity == Severity.WARNING),
        "informativos": sum(1 for f in result.findings if f.severity == Severity.INFO),
    }
    for kind in FindingKind:
        summary[kind.value] = sum(1 for f in result.findings if f.kind == kind)
    return summary
