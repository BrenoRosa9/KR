"""Procura uma expressão no texto extraído de um PDF, com o contexto ao redor.

Usada para calibrar as expressões regulares de contexto (área, perímetro,
matrícula, datum, meridiano central) contra a redação real dos documentos.

    python tools/find_text.py documento.pdf "matr[íi]cula"
"""

from __future__ import annotations

import re
import sys

import pdfplumber


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2

    path, pattern = sys.argv[1], sys.argv[2]
    regex = re.compile(pattern, re.IGNORECASE)

    with pdfplumber.open(path) as pdf:
        for number, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            for match in regex.finditer(text):
                start = max(0, match.start() - 70)
                snippet = " ".join(text[start : match.end() + 40].split())
                print(f"p.{number}: …{snippet}…")
    return 0


if __name__ == "__main__":
    sys.exit(main())
