"""Despeja as tabelas candidatas de um PDF, com o cabeçalho já interpretado.

Serve para responder à pergunta que mais aparece quando um documento falha:
o extrator achou a tabela e não entendeu as colunas, ou nem achou a tabela?

    python tools/inspect_tables.py documento.pdf [quantas]
"""

from __future__ import annotations

import sys

import pdfplumber

from app.extraction.pipeline import _map_with_two_row_fallback
from app.extraction.tables import extract_tables


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2

    path = sys.argv[1]
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 6

    with pdfplumber.open(path) as pdf:
        shown = 0
        for number, page in enumerate(pdf.pages, start=1):
            for table in extract_tables(page, number):
                mapping = _map_with_two_row_fallback(table)
                if mapping is None or not mapping.columns:
                    continue
                shown += 1
                if shown > limit:
                    return 0
                print(f"\n=== p.{number} tabela {table.index} via {table.strategy} ===")
                print(f"  linhas={table.row_count} coords={mapping.has_coordinates}")
                columns = {k: str(v) for k, v in mapping.columns.items()}
                print(f"  colunas={columns}")
                for row in table.rows()[:6]:
                    print("   |", " | ".join((cell.text or "")[:22] for cell in row))
    return 0


if __name__ == "__main__":
    sys.exit(main())
