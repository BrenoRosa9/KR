"""Núcleo determinístico: parsing, geodésia, correspondência e comparação.

Nada aqui toca banco de dados, rede ou sistema de arquivos. É código puro,
testável isoladamente e auditável — a propriedade que torna o laudo defensável.
Modelos de linguagem nunca produzem valores neste nível; no máximo alimentam a
extração, e sempre com validação determinística depois.
"""
