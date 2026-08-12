"""Detecção de região e extração de tabelas, com procedência por célula.

A ordem aqui é o que faz o módulo funcionar em documento real:

1. ``pdfplumber`` sobre a página inteira, que resolve tabelas com grade
   desenhada — a maioria dos PDFs digitais bem comportados.
2. Quando isso não produz uma tabela com cabeçalho de coordenadas, **localizar
   a faixa da tabela** procurando a linha de cabeçalho e descendo enquanto as
   linhas continuarem tabulares.
3. Só então extrair, restrito àquela faixa: por alinhamento de texto e por
   reconstrução geométrica a partir das palavras.

O estágio 2 não é refinamento, é pré-requisito. Sem recortar a faixa, o
parágrafo de abertura do memorial entra no cálculo das colunas e parte
``MEMORIAL DESCRITIVO`` em duas células — arruinando a tabela inteira. É também
o caminho para PDFs de CAD, em que cada rótulo é um span solto e a "tabela" são
apenas linhas desenhadas.

O que todos os caminhos têm em comum, e que é o requisito de verdade: cada
célula sai com sua *bounding box*, sem a qual não há tela de revisão nem laudo
rastreável.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core.numbers import parse_number
from ..core.schema import FieldKind
from .headers import map_header_row, match_header
from .text import clean_cell, collapse_spaced_letters, normalize_label

BBox = tuple[float, float, float, float]

# Tolerâncias de agrupamento em pontos PDF (1 pt = 1/72"). Uma linha de texto de
# 10 pt tem cerca de 12 pt de altura, então 4 pt separa linhas vizinhas com
# folga sem fundir duas linhas distintas.
ROW_TOLERANCE = 4.0
COLUMN_TOLERANCE = 12.0
MIN_ROWS_FOR_TABLE = 3
MIN_HEADER_FIELDS = 2
MIN_NUMBERS_PER_DATA_ROW = 2
MIN_NUMBER_DIGITS = 3
BAND_PADDING = 3.0

# Rodapés que fazem parte da tabela e devem entrar na faixa, para que a linha de
# totais seja lida (e depois descartada como vértice).
TABULAR_FOOTERS = frozenset(
    {"total", "totais", "perimetro", "soma", "somatorio", "fechamento"}
)


@dataclass
class Cell:
    text: str
    bbox: BBox | None
    row: int
    column: int
    # Preenchido só quando a célula vem de outra página que não a da tabela,
    # o que acontece em tabela emendada de uma página para a seguinte. Sem isto
    # o destaque na tela de revisão apontaria para a página errada.
    page: int | None = None

    @property
    def is_empty(self) -> bool:
        return not self.text.strip()


@dataclass
class ExtractedTable:
    page: int
    index: int
    cells: list[Cell] = field(default_factory=list)
    row_count: int = 0
    column_count: int = 0
    strategy: str = "pdfplumber"
    bbox: BBox | None = None

    def row(self, index: int) -> list[Cell]:
        return sorted(
            (cell for cell in self.cells if cell.row == index),
            key=lambda cell: cell.column,
        )

    def rows(self) -> list[list[Cell]]:
        return [self.row(index) for index in range(self.row_count)]

    def text_grid(self) -> list[list[str]]:
        return [[cell.text for cell in row] for row in self.rows()]

    def page_of(self, cell: Cell) -> int:
        return cell.page if cell.page is not None else self.page


def append_rows(base: ExtractedTable, extra: ExtractedTable) -> ExtractedTable:
    """Emenda as linhas de ``extra`` ao fim de ``base``, preservando a origem.

    Tabela de vértices grande atravessa páginas, e cada pedaço chega aqui como
    uma tabela independente. Emendá-las é o que permite fechar o polígono; sem
    isso, um memorial de oitenta vértices vira um polígono truncado de quarenta
    e todo o cálculo de área sai errado.
    """
    merged = ExtractedTable(
        page=base.page,
        index=base.index,
        cells=list(base.cells),
        row_count=base.row_count,
        column_count=max(base.column_count, extra.column_count),
        strategy=base.strategy,
        bbox=base.bbox,
    )

    for cell in extra.cells:
        merged.cells.append(
            Cell(
                text=cell.text,
                bbox=cell.bbox,
                row=base.row_count + cell.row,
                column=cell.column,
                page=extra.page_of(cell),
            )
        )

    merged.row_count = base.row_count + extra.row_count
    return merged


def extract_tables(page, page_number: int) -> list[ExtractedTable]:
    """Extrai tabelas de uma página, escalando de estratégia conforme necessário."""
    tables = _extract_with_pdfplumber(page, page_number)
    if _has_coordinate_header(tables):
        return tables

    for band_index, band in enumerate(find_table_bands(page)):
        try:
            cropped = page.crop(band)
        except Exception:  # pragma: no cover - faixa degenerada
            continue

        # A página recortada preserva as coordenadas originais, então as bboxes
        # continuam válidas para o destaque na tela de revisão.
        banded = _extract_with_pdfplumber(
            cropped, page_number, index_offset=10 * (band_index + 1)
        )
        tables.extend(banded)
        if _has_coordinate_header(banded):
            break

        geometric = reconstruct_from_words(
            cropped, page_number, index=90 + band_index
        )
        if geometric is not None:
            tables.append(geometric)
            if _has_coordinate_header([geometric]):
                break

    return tables


def _has_coordinate_header(tables: list[ExtractedTable]) -> bool:
    """A tabela serve se dá para mapear um cabeçalho com coordenadas.

    Critério deliberadamente estrito: contagem de linhas e taxa de preenchimento
    não distinguem uma tabela de vértices de um bloco de texto que por acaso
    ficou alinhado.
    """
    for table in tables:
        if table.row_count < MIN_ROWS_FOR_TABLE or table.column_count < 2:
            continue
        mapping = map_header_row(table.text_grid())
        if mapping is not None and mapping.has_coordinates:
            return True
    return False


def _extract_with_pdfplumber(
    page, page_number: int, index_offset: int = 0
) -> list[ExtractedTable]:
    results: list[ExtractedTable] = []

    # "lines" usa a grade desenhada; "text" alinha por posição. Documentos reais
    # aparecem nas duas formas, e tentar as duas é mais barato que adivinhar.
    settings = [
        {"vertical_strategy": "lines", "horizontal_strategy": "lines"},
        {"vertical_strategy": "text", "horizontal_strategy": "text"},
    ]

    seen: set[tuple] = set()
    for setting in settings:
        try:
            found = page.find_tables(table_settings=setting)
        except Exception:  # pragma: no cover - página malformada
            continue
        for table in found:
            key = tuple(round(value, 1) for value in (table.bbox or (0, 0, 0, 0)))
            if key in seen:
                continue
            seen.add(key)
            extracted = _from_pdfplumber_table(
                table, page_number, index_offset + len(results)
            )
            if extracted is not None:
                results.append(extracted)
    return results


def _from_pdfplumber_table(table, page_number: int, index: int) -> ExtractedTable | None:
    try:
        grid = table.extract()
    except Exception:  # pragma: no cover
        return None
    if not grid:
        return None

    result = ExtractedTable(
        page=page_number,
        index=index,
        row_count=len(grid),
        column_count=max(len(row) for row in grid),
        strategy="pdfplumber",
        bbox=tuple(float(v) for v in table.bbox) if table.bbox else None,  # type: ignore[arg-type]
    )

    # `table.rows[r].cells[c]` traz a bbox de cada célula; é dela que sai o
    # destaque na tela de revisão.
    for row_index, row in enumerate(grid):
        for column_index, raw in enumerate(row):
            bbox = None
            try:
                candidate = table.rows[row_index].cells[column_index]
                if candidate is not None:
                    bbox = tuple(float(v) for v in candidate)  # type: ignore[assignment]
            except (IndexError, AttributeError, TypeError):
                bbox = None
            result.cells.append(
                Cell(
                    text=collapse_spaced_letters(clean_cell(raw)),
                    bbox=bbox,
                    row=row_index,
                    column=column_index,
                )
            )
    return result


def find_table_bands(page) -> list[BBox]:
    """Localiza as faixas verticais da página que contêm tabelas.

    Procura linhas de texto que se pareçam com cabeçalho — duas ou mais colunas
    reconhecíveis como campo — e desce enquanto as linhas continuarem tabulares.
    É o estágio que separa a tabela do texto corrido ao redor.
    """
    rows = _word_rows(page)
    if len(rows) < MIN_ROWS_FOR_TABLE:
        return []

    bands: list[BBox] = []
    index = 0
    while index < len(rows):
        if len(_header_fields(rows[index])) < MIN_HEADER_FIELDS:
            index += 1
            continue

        end = index + 1
        # Cabeçalho de duas linhas: a segunda também é cabeçalho, não dado.
        if end < len(rows) and len(_header_fields(rows[end])) >= MIN_HEADER_FIELDS:
            end += 1
        while end < len(rows) and _is_data_row(rows[end]):
            end += 1

        if end - index >= MIN_ROWS_FOR_TABLE:
            bands.append(_band_bbox(rows[index:end], page))
            index = end
        else:
            index += 1

    return bands


def _word_rows(page) -> list[list[dict]]:
    try:
        words = page.extract_words(use_text_flow=False, keep_blank_chars=False)
    except Exception:  # pragma: no cover
        return []
    return _group_into_rows(words)


def _header_fields(row: list[dict]) -> set[FieldKind]:
    """Campos canônicos reconhecidos nesta linha de palavras.

    Testa janelas de até três palavras porque um cabeçalho real é ``Coordenada
    N (m)`` e não ``coordenada_n``. Sem a janela, ``N`` isolado seria ambíguo e
    ``Coordenada`` sozinho não mapearia nada.
    """
    texts = [str(word["text"]) for word in row]
    kinds: set[FieldKind] = set()
    for size in (3, 2, 1):
        for start in range(len(texts) - size + 1):
            kind = match_header(" ".join(texts[start : start + size]))
            if kind is not None:
                kinds.add(kind)
    return kinds


def _is_data_row(row: list[dict]) -> bool:
    texts = [str(word["text"]) for word in row]
    if not texts:
        return False

    if normalize_label(texts[0]) in TABULAR_FOOTERS:
        return True

    numeric = 0
    for text in texts:
        parsed = parse_number(text)
        if parsed is None:
            continue
        digits = sum(1 for char in text if char.isdigit())
        if digits >= MIN_NUMBER_DIGITS:
            numeric += 1
    return numeric >= MIN_NUMBERS_PER_DATA_ROW


def _band_bbox(rows: list[list[dict]], page) -> BBox:
    words = [word for row in rows for word in row]
    x0 = min(float(word["x0"]) for word in words) - BAND_PADDING
    x1 = max(float(word["x1"]) for word in words) + BAND_PADDING
    top = min(float(word["top"]) for word in words) - BAND_PADDING
    bottom = max(float(word["bottom"]) for word in words) + BAND_PADDING
    return (
        max(0.0, x0),
        max(0.0, top),
        min(float(page.width), x1),
        min(float(page.height), bottom),
    )


def reconstruct_from_words(
    page, page_number: int, index: int = 99
) -> ExtractedTable | None:
    """Reconstrói uma tabela a partir das palavras e suas posições.

    Caminho para PDF de CAD: agrupa palavras em linhas por proximidade vertical,
    depois deriva as colunas das bordas esquerdas mais recorrentes entre as
    linhas. É menos preciso que uma grade desenhada, mas é a diferença entre
    extrair algo e não extrair nada nesses documentos.
    """
    rows = [row for row in _word_rows(page) if len(row) >= 2]
    if len(rows) < MIN_ROWS_FOR_TABLE:
        return None

    boundaries = _infer_column_boundaries(rows)
    if len(boundaries) < 2:
        return None

    table = ExtractedTable(
        page=page_number,
        index=index,
        row_count=len(rows),
        column_count=len(boundaries),
        strategy="geometric",
    )

    for row_index, row in enumerate(rows):
        buckets: dict[int, list[dict]] = {}
        for word in row:
            column = _assign_column(float(word["x0"]), boundaries)
            buckets.setdefault(column, []).append(word)
        for column_index in range(len(boundaries)):
            bucket = sorted(
                buckets.get(column_index, []), key=lambda word: float(word["x0"])
            )
            text = collapse_spaced_letters(
                clean_cell(" ".join(str(word["text"]) for word in bucket))
            )
            bbox = _bbox_of(bucket) if bucket else None
            table.cells.append(
                Cell(text=text, bbox=bbox, row=row_index, column=column_index)
            )

    table.bbox = _bbox_of([word for row in rows for word in row])
    return table


def _group_into_rows(words: list[dict]) -> list[list[dict]]:
    ordered = sorted(words, key=lambda word: (float(word["top"]), float(word["x0"])))
    rows: list[list[dict]] = []
    current: list[dict] = []
    current_top: float | None = None

    for word in ordered:
        top = float(word["top"])
        if current_top is None or abs(top - current_top) <= ROW_TOLERANCE:
            current.append(word)
            # Média móvel: acompanha pequenas variações de linha de base sem
            # deixar a linha "escorregar" para a seguinte.
            current_top = top if current_top is None else (current_top + top) / 2.0
        else:
            rows.append(sorted(current, key=lambda item: float(item["x0"])))
            current = [word]
            current_top = top

    if current:
        rows.append(sorted(current, key=lambda item: float(item["x0"])))
    return rows


def _infer_column_boundaries(rows: list[list[dict]]) -> list[float]:
    """Deduz as bordas de coluna a partir das posições iniciais recorrentes."""
    starts: list[float] = [float(word["x0"]) for row in rows for word in row]
    starts.sort()

    clusters: list[list[float]] = []
    for start in starts:
        if clusters and start - clusters[-1][-1] <= COLUMN_TOLERANCE:
            clusters[-1].append(start)
        else:
            clusters.append([start])

    # Uma coluna de verdade aparece na maioria das linhas; um valor solto que
    # apareceu uma vez é ruído e não deve virar coluna.
    minimum_support = max(2, len(rows) // 3)
    boundaries = [
        sum(cluster) / len(cluster)
        for cluster in clusters
        if len(cluster) >= minimum_support
    ]
    return sorted(boundaries)


def _assign_column(x0: float, boundaries: list[float]) -> int:
    best_index, best_distance = 0, float("inf")
    for index, boundary in enumerate(boundaries):
        # Só considera colunas que começam à esquerda da palavra (com folga),
        # senão uma palavra curta seria atraída para a coluna seguinte.
        if x0 + COLUMN_TOLERANCE < boundary:
            continue
        distance = abs(x0 - boundary)
        if distance < best_distance:
            best_index, best_distance = index, distance
    return best_index


def _bbox_of(items: list[dict]) -> BBox:
    return (
        min(float(item["x0"]) for item in items),
        min(float(item["top"]) for item in items),
        max(float(item["x1"]) for item in items),
        max(float(item["bottom"]) for item in items),
    )
