"""Contexto do documento: datum, fuso, área, perímetro, matrícula.

Estes campos raramente estão em tabela. Ficam no texto corrido, no carimbo ou no
cabeçalho, e precisam ser lidos por padrão textual. Quando o datum não aparece
em lugar nenhum, o resultado é ``None`` — que o motor de comparação trata como
bloqueio. Chutar SIRGAS 2000 porque "é o mais comum" produziria laudo inválido
com aparência de válido.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..core.crs import (
    CRSSpec,
    CRSUndetermined,
    Hemisphere,
    normalize_datum_label,
    utm_epsg,
)
from ..core.numbers import (
    Convention,
    ParsedNumber,
    detect_convention,
    format_br,
    parse_number,
)
from ..core.schema import Measured, Provenance, SourceKind, TextValue
from .tax_ids import extract_tax_ids
from .text import normalize_label

_DATUM_RE = re.compile(
    r"(SIRGAS\s*-?\s*2000|SIRGAS|WGS\s*-?\s*84|SAD\s*-?\s*69|C[óo]rrego\s+Alegre)",
    re.IGNORECASE,
)
_ZONE_RE = re.compile(
    r"(?:fuso|zona)\s*(?:UTM)?\D{0,6}?(\d{1,2})\s*([NS])?",
    re.IGNORECASE,
)
_UTM_ZONE_RE = re.compile(r"UTM\s*-?\s*(\d{1,2})\s*([NS])", re.IGNORECASE)
# "MC-51°W", "MC 51° WGr", "meridiano central 51° W": o número é a longitude do
# meridiano central, não o fuso. Confundir os dois joga a parcela para o outro
# lado do planeta, e é um erro que passa despercebido porque 51 é um fuso válido.
_MERIDIAN_RE = re.compile(r"(?:MC|meridiano\s+central)", re.IGNORECASE)
_MERIDIAN_VALUE_RE = re.compile(r"(\d{1,3})\s*[°º]\s*(W|O|E|L)?", re.IGNORECASE)
_AREA_RE = re.compile(
    r"[áa]rea\D{0,20}?([\d.,\s]+\d)\s*(m²|m2|metros\s+quadrados|ha|hectares?)",
    re.IGNORECASE,
)
_PERIMETER_RE = re.compile(
    r"per[íi]metro\D{0,20}?([\d.,\s]+\d)\s*(m|metros)\b",
    re.IGNORECASE,
)
_MATRICULA_RE = re.compile(
    r"matr[íi]cula\D{0,15}?([\d.\-/]{3,20})",
    re.IGNORECASE,
)
_NUMBER_TOKEN_RE = re.compile(r"\d[\d.,]*\d")

# Contexto em que a matrícula citada é a do imóvel descrito...
_SUBJECT_CUES = (
    "descricao",
    "area encontrada",
    "imovel objeto",
    "objeto do presente",
    "memorial descritivo",
    "imovel descrito",
    "imovel",
    "parcela",
    "registrado sob a",
    "objeto da",
)
# ...e aquele em que é a de um vizinho.
_NEIGHBOUR_CUES = (
    "confront",
    "matriculado sob",
    "divisa",
    "limita",
    "lindeiro",
    "propriedade de",
)

HECTARE_M2 = 10_000.0


@dataclass
class DocumentContext:
    """Metadados de um documento, com procedência de cada um."""

    convention: Convention = "br"
    crs: CRSSpec | None = None
    datum_label: str | None = None
    utm_zone: int | None = None
    hemisphere: Hemisphere | None = None
    area: Measured | None = None
    perimeter: Measured | None = None
    matricula: TextValue | None = None
    tax_ids: list[TextValue] = field(default_factory=list)
    area_citations: list[Measured] = field(default_factory=list)
    perimeter_citations: list[Measured] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def detect_number_convention(text: str) -> Convention:
    """Decide a convenção decimal olhando todos os números do documento."""
    samples = _NUMBER_TOKEN_RE.findall(text)
    return detect_convention(samples)


def extract_context(
    pages_text: dict[int, str],
    document_id: str,
    northing_hint: float | None = None,
) -> DocumentContext:
    """Lê datum, fuso, área, perímetro e matrícula do texto das páginas."""
    joined = "\n".join(pages_text.values())
    context = DocumentContext(convention=detect_number_convention(joined))

    if context.convention == "ambiguous":
        context.convention = "br"
        context.warnings.append(
            "Não foi possível determinar a convenção decimal pelos números do "
            "documento (todos os grupos têm três dígitos). Assumido padrão "
            "brasileiro — confirme na revisão."
        )

    _read_datum(pages_text, context)
    _read_zone(pages_text, context, northing_hint)
    _resolve_crs(context)
    _read_area(pages_text, context, document_id)
    _read_perimeter(pages_text, context, document_id)
    _read_matricula(pages_text, context, document_id)
    context.tax_ids = extract_tax_ids(pages_text, document_id)
    return context


def _read_datum(pages_text: dict[int, str], context: DocumentContext) -> None:
    for text in pages_text.values():
        match = _DATUM_RE.search(text)
        if match is None:
            continue
        label = match.group(1)
        epsg = normalize_datum_label(label)
        if epsg is not None:
            context.datum_label = " ".join(label.split())
            return
    context.warnings.append(
        "Datum não localizado no texto do documento. É preciso informá-lo "
        "manualmente antes de comparar."
    )


def _read_zone(
    pages_text: dict[int, str],
    context: DocumentContext,
    northing_hint: float | None,
) -> None:
    for text in pages_text.values():
        match = _UTM_ZONE_RE.search(text) or _ZONE_RE.search(text)
        if match is None:
            continue
        zone = int(match.group(1))
        if not 1 <= zone <= 60:
            continue
        context.utm_zone = zone
        hemisphere = (match.group(2) or "").upper()
        if hemisphere in {"N", "S"}:
            context.hemisphere = hemisphere  # type: ignore[assignment]
        break

    if context.utm_zone is None:
        context.utm_zone = _zone_from_meridian(pages_text, context)

    if context.hemisphere is None and northing_hint is not None:
        # O falso norte de 10.000.000 m no hemisfério sul deixa a magnitude do
        # Northing como indício confiável quando o texto não declara.
        try:
            from ..core.crs import infer_hemisphere

            context.hemisphere = infer_hemisphere(northing_hint)
            context.warnings.append(
                f"Hemisfério não declarado; inferido como {context.hemisphere} pela "
                "magnitude do Northing."
            )
        except CRSUndetermined as exc:
            context.warnings.append(str(exc))

    if context.utm_zone is None:
        context.warnings.append(
            "Fuso UTM não localizado no documento. Informe manualmente."
        )


def _zone_from_meridian(
    pages_text: dict[int, str], context: DocumentContext
) -> int | None:
    """Deriva o fuso do meridiano central declarado no carimbo."""
    for text in pages_text.values():
        for match in _MERIDIAN_RE.finditer(text):
            # O carimbo é lido em ordem de coluna e costuma intercalar números
            # de outras legendas entre o rótulo e o valor ("MERIDIANO CENTRAL
            # 5,88 51° WGR"). Por isso procuramos um número seguido do símbolo
            # de grau na janela, e não o primeiro número que aparecer.
            window = text[match.end() : match.end() + 60]
            for value_match in _MERIDIAN_VALUE_RE.finditer(window):
                longitude = float(value_match.group(1))
                direction = (value_match.group(2) or "").upper()
                # No Brasil todo meridiano central é oeste; sem indicação é o que
                # assumimos, e a exigência de múltiplo ímpar de 3° peneira
                # qualquer número que tenha entrado por engano.
                east = direction in {"E", "L"}
                longitude = abs(longitude) if east else -abs(longitude)

                if longitude % 6 != 3:
                    continue

                zone = int(round((longitude + 183.0) / 6.0))
                if not 1 <= zone <= 60:
                    continue

                context.warnings.append(
                    f"Fuso não declarado diretamente; deduzido como {zone} a partir "
                    f"do meridiano central {value_match.group(0).strip()}."
                )
                return zone
    return None


def _resolve_crs(context: DocumentContext) -> None:
    if context.datum_label is None:
        return
    epsg = normalize_datum_label(context.datum_label)
    if epsg is None:
        return

    family = {
        "EPSG:4674": "SIRGAS2000",
        "EPSG:4326": "WGS84",
        "EPSG:4618": "SAD69",
        "EPSG:4225": "CORREGO_ALEGRE",
    }[epsg]

    if context.utm_zone is None or context.hemisphere is None:
        context.crs = CRSSpec(epsg=epsg, datum_label=family)
        return

    try:
        projected = utm_epsg(epsg, context.utm_zone, context.hemisphere)
    except CRSUndetermined as exc:
        context.warnings.append(str(exc))
        context.crs = CRSSpec(epsg=epsg, datum_label=family)
        return

    context.crs = CRSSpec(
        epsg=projected,
        datum_label=family,
        utm_zone=context.utm_zone,
        hemisphere=context.hemisphere,
    )


@dataclass(frozen=True)
class _Occurrence:
    """Uma ocorrência de um valor no texto, com onde ela foi encontrada."""

    key: str
    page: int
    raw: str


# Abaixo do limiar de revisão dos perfis: valor escolhido entre concorrentes
# nasce marcado para conferência humana.
DISPUTED_CONFIDENCE = 0.5


def _consensus(
    occurrences: list[_Occurrence],
) -> tuple[_Occurrence, float, list[str]] | None:
    """Escolhe entre ocorrências concorrentes do mesmo campo.

    Uma prancha de levantamento cita a área de todas as parcelas do quarteirão e
    a matrícula de cada confrontante. Pegar a primeira que aparece é sorteio.
    O critério aqui é a repetição — o valor da própria parcela é o que mais se
    repete — e, quando há disputa, o valor entra com confiança baixa e a lista
    dos concorrentes vai para o aviso, de modo que a decisão final seja humana.
    """
    if not occurrences:
        return None

    counts: dict[str, int] = {}
    for occurrence in occurrences:
        counts[occurrence.key] = counts.get(occurrence.key, 0) + 1

    # `counts` preserva a ordem de aparição e `max` fica com o primeiro máximo,
    # então o empate é desfeito por quem apareceu antes.
    winner_key = max(counts, key=lambda key: counts[key])
    winner = _first(occurrences, winner_key)

    if len(counts) == 1:
        return winner, 1.0, []

    rivals = [key for key in counts if key != winner_key]
    return winner, DISPUTED_CONFIDENCE, rivals


def _first(occurrences: list[_Occurrence], key: str) -> _Occurrence:
    return next(occurrence for occurrence in occurrences if occurrence.key == key)


def _read_area(
    pages_text: dict[int, str], context: DocumentContext, document_id: str
) -> None:
    occurrences: list[_Occurrence] = []
    values: dict[str, tuple[float, float, bool, str]] = {}

    for page, text in pages_text.items():
        for match in _AREA_RE.finditer(text):
            parsed = parse_number(match.group(1), context.convention)
            if parsed is None:
                continue
            unit = match.group(2).lower()
            in_hectares = unit.startswith("ha") or "hectare" in unit
            factor = HECTARE_M2 if in_hectares else 1.0
            value = parsed.value * factor
            key = f"{value:.4f}"
            occurrences.append(_Occurrence(key, page, match.group(0)))
            values.setdefault(
                key, (value, parsed.rounding_halfwidth * factor, in_hectares, parsed.raw)
            )

    decision = _consensus(occurrences)
    if decision is None:
        return

    winner, confidence, rivals = decision
    value, halfwidth, in_hectares, raw = values[winner.key]
    context.area = Measured(
        value=value,
        halfwidth=halfwidth,
        unit="m²",
        confidence=confidence,
        provenance=_text_provenance(document_id, winner.page, winner.raw),
    )
    # Guarda todas as citações distintas para confrontar texto × texto e
    # texto × tabela no motor de comparação.
    context.area_citations = [
        Measured(
            value=values[key][0],
            halfwidth=values[key][1],
            unit="m²",
            confidence=1.0 if key == winner.key else DISPUTED_CONFIDENCE,
            provenance=_text_provenance(
                document_id,
                _first(occurrences, key).page,
                _first(occurrences, key).raw,
            ),
        )
        for key in values
    ]

    if in_hectares:
        context.warnings.append(
            f"Área declarada em hectares ({raw} ha) convertida para "
            f"{format_br(value, 2)} m²."
        )
    if rivals:
        alternatives = ", ".join(f"{format_br(float(key), 2)} m²" for key in rivals[:4])
        context.warnings.append(
            f"O documento cita mais de uma área. Foi adotada a mais repetida "
            f"({format_br(value, 2)} m², página {winner.page}); também aparecem: "
            f"{alternatives}. Confirme na revisão."
        )


def _read_perimeter(
    pages_text: dict[int, str], context: DocumentContext, document_id: str
) -> None:
    occurrences: list[_Occurrence] = []
    values: dict[str, tuple[float, float]] = {}

    for page, text in pages_text.items():
        for match in _PERIMETER_RE.finditer(text):
            parsed = parse_number(match.group(1), context.convention)
            if parsed is None:
                continue
            key = f"{parsed.value:.4f}"
            occurrences.append(_Occurrence(key, page, match.group(0)))
            values.setdefault(key, (parsed.value, parsed.rounding_halfwidth))

    decision = _consensus(occurrences)
    if decision is None:
        return

    winner, confidence, rivals = decision
    value, halfwidth = values[winner.key]
    context.perimeter = Measured(
        value=value,
        halfwidth=halfwidth,
        unit="m",
        confidence=confidence,
        provenance=_text_provenance(document_id, winner.page, winner.raw),
    )
    context.perimeter_citations = [
        Measured(
            value=values[key][0],
            halfwidth=values[key][1],
            unit="m",
            confidence=1.0 if key == winner.key else DISPUTED_CONFIDENCE,
            provenance=_text_provenance(
                document_id,
                _first(occurrences, key).page,
                _first(occurrences, key).raw,
            ),
        )
        for key in values
    ]
    if rivals:
        context.warnings.append(
            f"O documento cita mais de um perímetro. Foi adotado "
            f"{format_br(value, 2)} m (página {winner.page}). Confirme na revisão."
        )


def _read_matricula(
    pages_text: dict[int, str], context: DocumentContext, document_id: str
) -> None:
    """Separa a matrícula do imóvel das matrículas dos confrontantes.

    Num memorial, a matrícula que mais aparece costuma ser a de um vizinho: cada
    lado do perímetro cita a sua. A do imóvel aparece onde ele é apresentado —
    "descrição da área", "imóvel objeto" — e é esse contexto, não a contagem,
    que a identifica.
    """
    subject: list[_Occurrence] = []
    neighbours: list[_Occurrence] = []

    for page, text in pages_text.items():
        for match in _MATRICULA_RE.finditer(text):
            value = match.group(1).strip(" .-/")
            if not value:
                continue
            before = normalize_label(text[max(0, match.start() - 90) : match.start()])
            occurrence = _Occurrence(value, page, match.group(0))
            if any(cue in before for cue in _NEIGHBOUR_CUES):
                neighbours.append(occurrence)
            elif any(cue in before for cue in _SUBJECT_CUES):
                subject.append(occurrence)
            else:
                neighbours.append(occurrence)

    decision = _consensus(subject) or _consensus(neighbours)
    if decision is None:
        return

    winner, confidence, rivals = decision
    # Documento que cita uma matrícula só não tem ambiguidade a resolver, e não
    # faz sentido mandá-lo para a revisão por causa do contexto da frase.
    identified = bool(subject) or len({item.key for item in neighbours}) == 1
    context.matricula = TextValue(
        value=winner.key,
        confidence=confidence if identified else DISPUTED_CONFIDENCE,
        provenance=_text_provenance(document_id, winner.page, winner.raw),
    )

    if not identified:
        context.warnings.append(
            f"Não foi possível distinguir a matrícula do imóvel das dos "
            f"confrontantes. Foi adotada a mais citada, n° {winner.key}. "
            "Confirme na revisão."
        )
    elif rivals:
        context.warnings.append(
            f"Mais de uma matrícula aparece como sendo do imóvel descrito. Foi "
            f"adotada a n° {winner.key}; também constam: {', '.join(rivals[:5])}. "
            "Confirme na revisão."
        )


def _text_provenance(document_id: str, page: int, raw: str) -> Provenance:
    """Procedência de valor lido em texto corrido.

    A bbox fica em branco: localizar o trecho exato exigiria recasar o texto com
    as palavras posicionadas, e para estes campos o número da página com o
    trecho literal já permite a conferência humana.
    """
    return Provenance(
        document_id=document_id,
        page=page,
        bbox=None,
        source_kind=SourceKind.TEXT_SPAN,
        raw_text=" ".join(raw.split()),
    )


def parse_measure(
    raw: str, convention: Convention, unit: str
) -> tuple[ParsedNumber, float] | None:
    parsed = parse_number(raw, convention)
    if parsed is None:
        return None
    return parsed, parsed.rounding_halfwidth
