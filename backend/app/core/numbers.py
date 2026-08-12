"""Parsing de números em formatos heterogêneos.

O ponto central deste módulo é que a *precisão da origem* é tão importante
quanto o valor. Um documento que imprime ``1.234,56`` declara duas casas
decimais, e comparar esse valor com um recálculo de três casas exige tolerância
derivada dessas duas casas — não de uma constante global. Por isso todo parsing
devolve ``ParsedNumber``, que carrega ``decimals`` junto com ``value``.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

Convention = Literal["br", "us", "plain", "ambiguous"]

# Espaços que aparecem como separador de milhar em PDFs gerados por CAD.
_SPACE_CHARS = "\u00a0\u2007\u202f\u2009\u200a "

_NUMERIC_RE = re.compile(
    r"""
    (?P<sign>[-+\u2212])?          # inclui o minus sign tipográfico U+2212
    (?P<body>\d[\d.,\s\u00a0\u2007\u202f\u2009\u200a]*)
    """,
    re.VERBOSE,
)


@dataclass(frozen=True)
class ParsedNumber:
    """Número extraído de um documento, com a precisão declarada na origem."""

    value: float
    decimals: int
    raw: str
    convention: Convention

    @property
    def rounding_halfwidth(self) -> float:
        """Metade do último dígito significativo.

        Um valor impresso com ``d`` casas está a até ``0,5 * 10**-d`` do valor
        real. Comparar dois valores independentemente arredondados soma as duas
        meias-larguras, o que o motor de comparação usa como piso de tolerância.
        """
        return 0.5 * (10.0**-self.decimals)


def _strip_spaces(text: str) -> str:
    for char in _SPACE_CHARS:
        text = text.replace(char, "")
    return text


def detect_convention(samples: Iterable[str]) -> Convention:
    """Infere a convenção decimal olhando o documento inteiro, não campo a campo.

    Uma string isolada como ``1,234`` é genuinamente ambígua. O conjunto de
    strings de um documento quase nunca é: basta um único ``1.234,56`` ou um
    único ``0,5`` para decidir. Retorna ``ambiguous`` quando nem isso existe,
    caso em que o chamador deve pedir confirmação humana em vez de assumir.
    """
    votes: Counter[Convention] = Counter()

    for raw in samples:
        text = _strip_spaces(raw)
        has_dot = "." in text
        has_comma = "," in text

        if has_dot and has_comma:
            # O separador decimal é o que aparece por último.
            votes["br" if text.rindex(",") > text.rindex(".") else "us"] += 1
            continue

        if has_comma and not has_dot:
            if text.count(",") > 1:
                votes["us"] += 1  # múltiplas vírgulas só podem ser milhar
            else:
                after = text.split(",")[1]
                # 3 dígitos após o separador é ambíguo; qualquer outra
                # quantidade só faz sentido como decimal.
                if len(after) != 3:
                    votes["br"] += 1
            continue

        if has_dot and not has_comma:
            if text.count(".") > 1:
                votes["br"] += 1  # múltiplos pontos só podem ser milhar
            else:
                after = text.split(".")[1]
                if len(after) != 3:
                    votes["us"] += 1

    if not votes:
        return "ambiguous"
    winner, _ = votes.most_common(1)[0]
    return winner


def parse_number(raw: str, convention: Convention = "br") -> ParsedNumber | None:
    """Extrai o primeiro número de ``raw``. Retorna ``None`` se não houver."""
    if raw is None:
        return None

    match = _NUMERIC_RE.search(raw)
    if match is None:
        return None

    sign = -1.0 if (match.group("sign") or "") in {"-", "\u2212"} else 1.0
    body = _strip_spaces(match.group("body")).rstrip(".,")
    if not body:
        return None

    decimal_sep = _decimal_separator(body, convention)

    if decimal_sep is None:
        digits, decimals = body.replace(".", "").replace(",", ""), 0
    else:
        thousands_sep = "," if decimal_sep == "." else "."
        head, _, tail = body.rpartition(decimal_sep)
        digits = head.replace(thousands_sep, "") + "." + tail
        decimals = len(tail)

    try:
        value = sign * float(digits)
    except ValueError:
        return None

    return ParsedNumber(
        value=value, decimals=decimals, raw=raw.strip(), convention=convention
    )


def _decimal_separator(body: str, convention: Convention) -> str | None:
    """Decide qual caractere é o separador decimal nesta string específica."""
    has_dot, has_comma = "." in body, "," in body

    if has_dot and has_comma:
        return "," if body.rindex(",") > body.rindex(".") else "."

    if has_comma:
        if body.count(",") > 1:
            return None  # todos são separadores de milhar
        after = body.split(",")[1]
        if len(after) == 3 and convention == "us":
            return None
        return ","

    if has_dot:
        if body.count(".") > 1:
            return None
        after = body.split(".")[1]
        if len(after) == 3 and convention in {"br", "ambiguous"}:
            return None
        return "."

    return None


def format_br(value: float, decimals: int = 3) -> str:
    """Formata para exibição em pt-BR (usado em relatórios)."""
    text = f"{value:,.{decimals}f}"
    return text.replace(",", "\x00").replace(".", ",").replace("\x00", ".")
