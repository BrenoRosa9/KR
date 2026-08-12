"""Orquestração da extração, em estágios idempotentes.

Cada estágio grava o seu resultado e pode ser reexecutado isoladamente. Se o
parser de tabelas mudar, não é preciso rodar OCR de oitenta páginas outra vez —
essa propriedade é o que torna a operação viável em documentos pesados e o que
permite corrigir um parser sem reprocessar o acervo.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path

import pdfplumber

from ..core.schema import FieldKind, Parcel, SourceKind
from .context import DocumentContext, extract_context
from .headers import HeaderMapping, map_header_row, merge_two_row_header
from .tables import ExtractedTable, append_rows, extract_tables
from .triage import DocumentProfile, PageClass, profile_document
from .vertices import VertexTableResult, build_parcel_from_table

# Confiança inicial por origem. OCR entra abaixo do limite padrão de revisão de
# propósito: todo número lido por OCR passa por conferência humana antes de
# constar no laudo.
CONFIDENCE_BY_SOURCE = {
    SourceKind.TABLE_CELL: 1.0,
    SourceKind.TEXT_SPAN: 0.95,
    SourceKind.OCR: 0.70,
    SourceKind.VISION_MODEL: 0.60,
}


class Stage(StrEnum):
    INGEST = "ingest"
    TRIAGE = "triage"
    OCR = "ocr"
    TABLES = "tables"
    CONTEXT = "context"
    PARCEL = "parcel"


@dataclass
class StageLog:
    stage: Stage
    ok: bool
    message: str = ""


@dataclass
class TableCandidate:
    table: ExtractedTable
    mapping: HeaderMapping

    @property
    def score(self) -> float:
        """Quantas colunas úteis a tabela tem, ponderada por ter coordenadas.

        Uma tabela de confrontantes pode ter mais linhas que a de vértices; o
        que decide é conter coordenadas, não tamanho.
        """
        bonus = 10.0 if self.mapping.has_coordinates else 0.0
        return bonus + len(self.mapping.columns) + self.table.row_count / 100.0


@dataclass
class ExtractionResult:
    document_id: str
    path: str
    profile: DocumentProfile | None = None
    context: DocumentContext | None = None
    parcel: Parcel | None = None
    candidates: list[TableCandidate] = field(default_factory=list)
    chosen: TableCandidate | None = None
    stages: list[StageLog] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.parcel is not None and not self.errors

    def log(self, stage: Stage, ok: bool, message: str = "") -> None:
        self.stages.append(StageLog(stage=stage, ok=ok, message=message))


def extract_document(
    path: str | Path,
    document_id: str,
    label: str = "",
    ocr_enabled: bool = True,
    ocr_output_dir: str | Path | None = None,
) -> ExtractionResult:
    """Executa o pipeline completo sobre um PDF."""
    path = Path(path)
    result = ExtractionResult(document_id=document_id, path=str(path))

    if not path.exists():
        result.errors.append(f"Arquivo não encontrado: {path}")
        result.log(Stage.INGEST, False, "arquivo ausente")
        return result
    result.log(Stage.INGEST, True, f"{path.name}, {path.stat().st_size} bytes")

    try:
        profile = profile_document(path)
    except Exception as exc:
        result.errors.append(f"Falha ao abrir o PDF: {exc}")
        result.log(Stage.TRIAGE, False, str(exc))
        return result

    result.profile = profile
    result.log(
        Stage.TRIAGE,
        True,
        f"{profile.page_count} página(s), predominante: {profile.dominant_class}",
    )

    working_path = path
    source_kind = SourceKind.TABLE_CELL

    if profile.needs_ocr:
        if not ocr_enabled:
            result.warnings.append(
                "Documento tem páginas escaneadas mas o OCR está desativado; essas "
                "páginas foram ignoradas."
            )
            result.log(Stage.OCR, False, "desativado")
        else:
            ocr_path, message = _run_ocr(path, ocr_output_dir)
            if ocr_path is None:
                result.warnings.append(message)
                result.log(Stage.OCR, False, message)
            else:
                working_path = ocr_path
                source_kind = SourceKind.OCR
                result.log(Stage.OCR, True, message)
    else:
        result.log(Stage.OCR, True, "não necessário: camada de texto presente")

    try:
        candidates, pages_text, all_tables = _scan_pages(working_path, profile)
    except Exception as exc:
        result.errors.append(f"Falha na extração de tabelas: {exc}")
        result.log(Stage.TABLES, False, str(exc))
        return result

    result.candidates = candidates
    result.log(
        Stage.TABLES,
        bool(candidates),
        f"{len(candidates)} tabela(s) com cabeçalho reconhecido",
    )

    northing_hint = _northing_hint(candidates)
    context = extract_context(pages_text, document_id, northing_hint)
    result.context = context
    result.warnings.extend(context.warnings)
    result.log(
        Stage.CONTEXT,
        context.crs is not None,
        context.crs.describe() if context.crs else "CRS não determinado",
    )

    chosen = _best_candidate(candidates)
    if chosen is None:
        result.errors.append(
            "Nenhuma tabela de vértices com coordenadas foi encontrada. O documento "
            "pode estar escaneado sem OCR, ter layout não suportado, ou ser uma "
            "planta sem tabela."
        )
        result.log(Stage.PARCEL, False, "sem tabela de vértices")
        return result

    result.chosen = chosen
    _warn_about_other_parcels(candidates, chosen, result)

    table, stitched_pages = _stitch_continuations(
        chosen, all_tables, context.convention
    )
    if stitched_pages:
        pages = ", ".join(str(page) for page in stitched_pages)
        result.warnings.append(
            f"A tabela de vértices continua nas páginas {pages} sem repetir o "
            "cabeçalho; as linhas foram emendadas à tabela principal."
        )

    built: VertexTableResult = build_parcel_from_table(
        table=table,
        mapping=chosen.mapping,
        context=context,
        document_id=document_id,
        label=label or path.stem,
        base_confidence=CONFIDENCE_BY_SOURCE.get(source_kind, 1.0),
        source_kind=source_kind,
    )
    result.parcel = built.parcel
    result.warnings.extend(built.warnings)
    result.log(
        Stage.PARCEL,
        bool(built.parcel.vertices),
        f"{built.rows_read} vértice(s), {built.rows_skipped} linha(s) descartada(s), "
        f"convenção de lado: {built.segment_convention}",
    )
    return result


def _scan_pages(
    path: Path, profile: DocumentProfile
) -> tuple[list[TableCandidate], dict[int, str], list[ExtractedTable]]:
    """Varre as páginas relevantes, coletando tabelas mapeáveis e texto.

    Devolve também as tabelas cruas: as continuações de uma tabela grande não
    repetem o cabeçalho e por isso não viram candidatas, mas são justamente o
    que falta para completar o polígono.
    """
    candidates: list[TableCandidate] = []
    pages_text: dict[int, str] = {}
    all_tables: list[ExtractedTable] = []
    relevant = set(profile.data_pages())

    with pdfplumber.open(path) as pdf:
        for index, page in enumerate(pdf.pages, start=1):
            # Texto de contexto (CPF, área, matrícula) vem de todas as páginas.
            # Tabelas de vértices só nas páginas classificadas como dados —
            # plantas e capas ilustradas não precisam passar pelo parser.
            pages_text[index] = page.extract_text() or ""
            if index not in relevant and profile.page_count > 1:
                continue
            for table in extract_tables(page, index):
                all_tables.append(table)
                mapping = _map_with_two_row_fallback(table)
                if mapping is not None:
                    candidates.append(TableCandidate(table=table, mapping=mapping))
    return candidates, pages_text, all_tables


def _map_with_two_row_fallback(table: ExtractedTable) -> HeaderMapping | None:
    """Tenta o cabeçalho em uma linha e também em duas linhas, e fica com o melhor.

    Não basta aceitar a primeira linha que traga coordenadas. Em plantas de CAD é
    comum ``LADOS | COORDENADAS | AZIMUTE | DISTÂNCIA`` acima de ``Vértice |
    Longitude | Latitude | E | N``: a linha de baixo, sozinha, dá as coordenadas
    e joga azimute e distância fora.
    """
    grid = table.text_grid()
    if not grid:
        return None

    best = map_header_row(grid)

    for header_row in range(min(4, len(grid) - 1)):
        merged = merge_two_row_header(grid, header_row)
        candidate = map_header_row([merged])
        if candidate is None:
            continue
        # A linha de dados começa depois das *duas* linhas de cabeçalho.
        candidate = replace(candidate, header_row=header_row + 1)
        if _is_richer(candidate, best):
            best = candidate

    return best


def _is_richer(candidate: HeaderMapping, current: HeaderMapping | None) -> bool:
    if current is None:
        return True
    if candidate.has_coordinates != current.has_coordinates:
        return candidate.has_coordinates
    return len(candidate.columns) > len(current.columns)


def _stitch_continuations(
    chosen: TableCandidate,
    all_tables: list[ExtractedTable],
    convention: str,
) -> tuple[ExtractedTable, list[int]]:
    """Emenda à tabela escolhida as suas continuações nas páginas seguintes.

    O que caracteriza uma continuação é a evidência, não a posição: mesmo número
    de colunas, nenhum cabeçalho próprio de coordenadas — porque isso indicaria
    outra tabela — e valores que continuam caindo nas mesmas colunas numéricas,
    com magnitude compatível. O critério é conservador de propósito: emendar
    tabelas de parcelas diferentes produziria um polígono inventado.
    """
    reference = _coordinate_columns(chosen)
    if not reference:
        return chosen.table, []

    anchors = _column_samples(
        chosen.table, chosen.mapping.header_row + 1, reference, convention
    )
    if not anchors:
        return chosen.table, []

    by_page: dict[int, list[ExtractedTable]] = {}
    for table in all_tables:
        by_page.setdefault(table.page, []).append(table)

    merged = chosen.table
    stitched: list[int] = []

    # A cadeia é de páginas consecutivas e para na primeira que não continua.
    # Sem essa exigência, uma tabela de outra parcela lá na página trinta — do
    # mesmo bairro, portanto com coordenadas parecidas — entraria no polígono.
    page = chosen.table.page + 1
    while page in by_page:
        continuation = next(
            (
                table
                for table in sorted(by_page[page], key=lambda item: item.index)
                if _is_continuation(chosen, table, reference, anchors, convention)
            ),
            None,
        )
        if continuation is None:
            break
        merged = append_rows(merged, continuation)
        stitched.append(page)
        page += 1

    return merged, stitched


def _is_continuation(
    chosen: TableCandidate,
    table: ExtractedTable,
    reference: dict[int, FieldKind],
    anchors: dict[int, list[float]],
    convention: str,
) -> bool:
    if table.column_count != chosen.table.column_count:
        return False

    mapping = map_header_row(table.text_grid())
    if mapping is not None and mapping.has_coordinates:
        # Cabeçalho próprio com coordenadas: é outra tabela, não continuação.
        return False

    samples = _column_samples(table, 0, reference, convention)
    return _matches_anchor(samples, anchors)


def _coordinate_columns(candidate: TableCandidate) -> dict[int, FieldKind]:
    wanted = {
        FieldKind.EASTING,
        FieldKind.NORTHING,
        FieldKind.LATITUDE,
        FieldKind.LONGITUDE,
    }
    return {
        column: kind
        for column, kind in candidate.mapping.columns.items()
        if kind in wanted
    }


def _column_samples(
    table: ExtractedTable,
    first_row: int,
    columns: dict[int, FieldKind],
    convention: str,
) -> dict[int, list[float]]:
    """Valores numéricos lidos nas colunas de coordenada, para comparação."""
    from ..core.numbers import parse_number

    samples: dict[int, list[float]] = {column: [] for column in columns}
    for row_index in range(first_row, min(first_row + 12, table.row_count)):
        for cell in table.row(row_index):
            if cell.column not in samples:
                continue
            parsed = parse_number(cell.text, convention)
            if parsed is not None:
                samples[cell.column].append(parsed.value)
    return {column: values for column, values in samples.items() if values}


def _matches_anchor(
    samples: dict[int, list[float]], anchors: dict[int, list[float]]
) -> bool:
    """As colunas numéricas continuam na mesma faixa de grandeza?

    Coordenadas de uma mesma parcela não pulam mais que alguns quilômetros. Um
    salto maior denuncia outra tabela alinhada por coincidência.
    """
    if set(samples) != set(anchors):
        return False

    for column, values in samples.items():
        reference = sum(anchors[column]) / len(anchors[column])
        spread = max(abs(value - reference) for value in values)
        scale = max(abs(reference), 1.0)
        if spread > max(5000.0, 0.02 * scale):
            return False
    return True


def _warn_about_other_parcels(
    candidates: list[TableCandidate],
    chosen: TableCandidate,
    result: ExtractionResult,
) -> None:
    """Uma prancha pode descrever várias parcelas; só uma foi comparada."""
    others = [
        candidate
        for candidate in candidates
        if candidate.mapping.has_coordinates and candidate is not chosen
    ]
    if not others:
        return

    result.warnings.append(
        f"O documento tem {len(others) + 1} tabelas de vértices com coordenadas "
        f"(possivelmente parcelas distintas na mesma prancha). Foi usada a da "
        f"página {chosen.table.page}, com {chosen.table.row_count} linhas. "
        "Confirme na revisão se é a parcela pretendida."
    )


def _best_candidate(candidates: list[TableCandidate]) -> TableCandidate | None:
    with_coordinates = [c for c in candidates if c.mapping.has_coordinates]
    if not with_coordinates:
        return None
    return max(with_coordinates, key=lambda candidate: candidate.score)


def _northing_hint(candidates: list[TableCandidate]) -> float | None:
    """Um Northing qualquer, para inferir o hemisfério quando o texto não diz."""
    from ..core.numbers import parse_number
    from ..core.schema import FieldKind

    for candidate in candidates:
        column = candidate.mapping.column_of(FieldKind.NORTHING)
        if column is None:
            continue
        first_data_row = candidate.mapping.header_row + 1
        for row_index in range(first_data_row, candidate.table.row_count):
            for cell in candidate.table.row(row_index):
                if cell.column != column or cell.is_empty:
                    continue
                parsed = parse_number(cell.text)
                if parsed is not None and parsed.value > 1000.0:
                    return parsed.value
    return None


def _run_ocr(path: Path, output_dir: str | Path | None) -> tuple[Path | None, str]:
    """Aplica OCR gerando um PDF com camada de texto.

    Import tardio e falha tolerada de propósito: OCRmyPDF depende de binários do
    sistema (Tesseract, Ghostscript) que existem na imagem Docker mas não
    necessariamente na máquina de quem roda os testes. Sem eles, o pipeline
    segue tratando o documento como digital em vez de abortar.
    """
    try:
        import ocrmypdf
    except ImportError:
        return None, (
            "OCR indisponível: pacote ocrmypdf não instalado neste ambiente. "
            "Páginas escaneadas não foram lidas."
        )

    destination = Path(output_dir or path.parent) / f"{path.stem}.ocr.pdf"
    if destination.exists():
        return destination, f"OCR reaproveitado de {destination.name}"

    try:
        ocrmypdf.ocr(
            path,
            destination,
            language="por",
            deskew=True,
            rotate_pages=True,
            skip_text=True,  # não re-OCR de páginas que já têm texto
            optimize=1,
            progress_bar=False,
        )
    except Exception as exc:  # pragma: no cover - depende de binários externos
        return None, f"OCR falhou: {exc}"

    return destination, f"OCR aplicado, resultado em {destination.name}"


def page_classification_summary(profile: DocumentProfile) -> dict[str, int]:
    summary: dict[str, int] = {}
    for page in profile.pages:
        key = str(page.classification)
        summary[key] = summary.get(key, 0) + 1
    for page_class in PageClass:
        summary.setdefault(str(page_class), 0)
    return summary
