"""Identificadores fiscais (CPF/CNPJ) citados no documento.

Memoriais e plantas listam proprietários em vários lugares — capa, quadro de
áreas, legenda da planta. A comparação útil não é string a string: é o conjunto
de documentos normalizados (só dígitos) presentes em A contra o de B.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..core.schema import Provenance, SourceKind, TextValue

# Formatos com e sem máscara. O dígito verificador é checado depois.
_CPF_RE = re.compile(
    r"(?<!\d)(\d{3}\.?\d{3}\.?\d{3}-?\d{2})(?!\d)",
)
_CNPJ_RE = re.compile(
    r"(?<!\d)(\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2})(?!\d)",
)


@dataclass(frozen=True)
class TaxIdHit:
    """Uma citação de CPF ou CNPJ, já normalizada."""

    kind: str  # "cpf" | "cnpj"
    digits: str
    display: str
    page: int
    raw: str
    valid: bool


def extract_tax_ids(pages_text: dict[int, str], document_id: str) -> list[TextValue]:
    """Lê todos os CPF/CNPJ do texto, deduplicando por dígitos+página.

    Mantém uma ocorrência por (dígitos, página): se o mesmo CPF aparece dez
    vezes na mesma página, uma basta para a rastreabilidade; se aparece em
    páginas distintas, as duas ficam — serve para mostrar “capa × quadro”.
    """
    hits = _collect_hits(pages_text)
    seen: set[tuple[str, int]] = set()
    values: list[TextValue] = []

    for hit in hits:
        key = (hit.digits, hit.page)
        if key in seen:
            continue
        seen.add(key)
        values.append(
            TextValue(
                value=hit.digits,
                confidence=1.0 if hit.valid else 0.55,
                provenance=Provenance(
                    document_id=document_id,
                    page=hit.page,
                    source_kind=SourceKind.TEXT_SPAN,
                    raw_text=hit.raw,
                ),
            )
        )
    return values


def _collect_hits(pages_text: dict[int, str]) -> list[TaxIdHit]:
    hits: list[TaxIdHit] = []
    for page, text in pages_text.items():
        for match in _CNPJ_RE.finditer(text):
            raw = match.group(1)
            digits = re.sub(r"\D", "", raw)
            if len(digits) != 14:
                continue
            # Evita que um CPF mascarado parcial entre como CNPJ.
            if _looks_like_cpf_fragment(digits):
                continue
            hits.append(
                TaxIdHit(
                    kind="cnpj",
                    digits=digits,
                    display=_format_cnpj(digits),
                    page=page,
                    raw=raw,
                    valid=_cnpj_valid(digits),
                )
            )
        for match in _CPF_RE.finditer(text):
            raw = match.group(1)
            digits = re.sub(r"\D", "", raw)
            if len(digits) != 11:
                continue
            # Sequências já capturadas como parte de CNPJ.
            if any(hit.digits.find(digits) >= 0 and hit.kind == "cnpj" for hit in hits):
                continue
            hits.append(
                TaxIdHit(
                    kind="cpf",
                    digits=digits,
                    display=_format_cpf(digits),
                    page=page,
                    raw=raw,
                    valid=_cpf_valid(digits),
                )
            )
    return hits


def _looks_like_cpf_fragment(digits: str) -> bool:
    return False


def _format_cpf(digits: str) -> str:
    return f"{digits[:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:]}"


def _format_cnpj(digits: str) -> str:
    return (
        f"{digits[:2]}.{digits[2:5]}.{digits[5:8]}/{digits[8:12]}-{digits[12:]}"
    )


def _cpf_valid(digits: str) -> bool:
    if len(digits) != 11 or digits == digits[0] * 11:
        return False
    nums = [int(d) for d in digits]
    s1 = sum(n * w for n, w in zip(nums[:9], range(10, 1, -1)))
    d1 = (s1 * 10) % 11 % 10
    if d1 != nums[9]:
        return False
    s2 = sum(n * w for n, w in zip(nums[:10], range(11, 1, -1)))
    d2 = (s2 * 10) % 11 % 10
    return d2 == nums[10]


def _cnpj_valid(digits: str) -> bool:
    if len(digits) != 14 or digits == digits[0] * 14:
        return False
    nums = [int(d) for d in digits]
    w1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    w2 = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    d1 = sum(n * w for n, w in zip(nums[:12], w1)) % 11
    d1 = 0 if d1 < 2 else 11 - d1
    if d1 != nums[12]:
        return False
    d2 = sum(n * w for n, w in zip(nums[:13], w2)) % 11
    d2 = 0 if d2 < 2 else 11 - d2
    return d2 == nums[13]


def format_tax_id(digits: str) -> str:
    if len(digits) == 11:
        return _format_cpf(digits)
    if len(digits) == 14:
        return _format_cnpj(digits)
    return digits
