"""Normalização de texto para casamento de rótulos.

Documentos escrevem o mesmo cabeçalho de dezenas de formas: ``VÉRTICE``,
``Vertice``, ``Vért.``, ``V é r t i c e`` (quando o PDF vem de CAD com
espaçamento por caractere). Comparar rótulos exige achatar tudo isso antes.
"""

from __future__ import annotations

import re
import unicodedata

_PUNCTUATION_RE = re.compile(r"[.\-_/\\|()\[\]:;,]+")
_WHITESPACE_RE = re.compile(r"\s+")


def strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def normalize_label(text: str) -> str:
    """Achata um rótulo para comparação: sem acento, sem pontuação, minúsculo."""
    if not text:
        return ""
    flattened = strip_accents(text).lower()
    flattened = _PUNCTUATION_RE.sub(" ", flattened)
    flattened = _WHITESPACE_RE.sub(" ", flattened).strip()
    return flattened


def collapse_spaced_letters(text: str) -> str:
    """Junta ``V É R T I C E`` em ``VÉRTICE``.

    PDFs exportados de CAD frequentemente posicionam cada caractere
    individualmente, e o extrator devolve o rótulo com espaço entre todas as
    letras. Sem esta correção nenhum cabeçalho seria reconhecido nesses
    documentos.
    """
    tokens = text.split()
    if len(tokens) >= 3 and all(len(token) == 1 for token in tokens):
        return "".join(tokens)
    return text


def clean_cell(text: str | None) -> str:
    if not text:
        return ""
    return _WHITESPACE_RE.sub(" ", text.replace("\n", " ")).strip()
