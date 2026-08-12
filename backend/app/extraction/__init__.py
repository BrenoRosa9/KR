"""Extração de dados de PDFs técnicos.

A ordem dos estágios é a decisão de projeto que mais importa aqui: triagem
antes de tudo, para não rodar OCR onde já existe texto nem tentar ler texto onde
não existe; determinístico antes de modelo, para que a IA só seja chamada onde o
código convencional falhou; e procedência em cada valor, sem exceção, porque um
número sem origem rastreável não pode entrar num laudo.
"""
