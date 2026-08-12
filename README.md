# Comparador de documentos técnicos de georreferenciamento

Recebe dois PDFs (memorial descritivo, planta, laudo), extrai vértices,
coordenadas, azimutes, distâncias, ângulos, áreas, perímetros e confrontantes,
recalcula a geometria e aponta divergências — separando o que é **diferença
entre os dois documentos** do que é **inconsistência interna de um deles**.

Todo valor exibido é rastreável até a origem: documento, página e célula.

## Como o sistema pensa

O ponto que orienta o projeto inteiro: **nenhum número é inventado**. Quando o
documento não declara o datum, o sistema não escolhe um; ele para e pede a
confirmação. Quando um valor é lido com baixa confiança, ele aparece marcado
na tela de revisão em vez de entrar silenciosamente no laudo.

A comparação é feita em quatro vias:

| Confronto | O que revela |
| --- | --- |
| Declarado A × recalculado A | Inconsistência interna de A |
| Declarado B × recalculado B | Inconsistência interna de B |
| Recalculado A × recalculado B | Diferença real de geometria |
| Declarado A × declarado B | Diferença de texto/transcrição |

Antes de listar vértice por vértice, o sistema procura uma **causa única**:
translação, rotação ou escala que explique quase toda a diferença. Datum
trocado, fuso errado ou distância no terreno confrontada com distância na
projeção produzem centenas de divergências que na verdade são uma só.

A comparação exige **igualdade exata** dos números: qualquer diferença entre os
documentos, ou entre o valor declarado e o recalculado a partir das coordenadas,
é apontada. Não há faixa de tolerância configurável.

## Stack

- **Backend**: Python 3.12, FastAPI, SQLAlchemy 2, PostgreSQL 16 + PostGIS
- **Extração**: pdfplumber (texto, palavras, tabelas), pypdfium2 (rasterização),
  ocrmypdf + Tesseract (escaneados), pikepdf (metadados)
- **Cálculo**: pyproj, shapely, numpy, scipy
- **Frontend**: React 19, TypeScript, Vite, Tailwind 4, react-pdf
- **Laudo**: Jinja2 + WeasyPrint
- **Entrega**: Docker Compose atrás do Caddy

Nenhuma dependência AGPL: as bibliotecas usadas são MIT, BSD ou Apache-2.0, o
que mantém o código livre para uso comercial fechado.

## Arquitetura

```
navegador ──► Caddy ──┬──► /api  ──► FastAPI (uvicorn)
                      └──► /     ──► arquivos estáticos do React
                                        │
                        Postgres ◄──────┤ (dados + fila de trabalhos)
                                        │
                        worker ─────────┘ (extração, OCR, comparação)
                                        │
                        /data/blobs ────┘ (PDFs originais, por hash)
```

A fila vive no próprio Postgres (`SELECT ... FOR UPDATE SKIP LOCKED`). Para o
volume desta empresa — poucos usuários, poucas análises por dia — subir Redis ou
Celery só acrescentaria uma peça a mais para manter no ar.

O worker é um processo separado porque OCR e extração ocupam a CPU por minutos;
dentro do servidor web, travariam a interface.

## Subindo em produção

Na VM Linux (Hyper-V, Ubuntu Server LTS) do servidor Windows:

```bash
git clone <repo> kr && cd kr
cp .env.example .env
# edite .env: senha do banco, KR_SESSION_SECRET, SITE_ADDRESS
docker compose up -d --build
docker compose exec api python -m app.cli create-user \
    --email voce@empresa.com.br --name "Seu Nome" --role admin
```

Verificação: `curl -fsS http://localhost/api/health` deve responder
`{"status":"ok", ...}`.

## Desenvolvimento local

Backend:

```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[server,report,dev]"
$env:KR_DATABASE_URL = "sqlite+pysqlite:///./dev.db"
$env:KR_STORAGE_ROOT = "./data"
python -m app.cli init-db
python -m app.cli create-user --email dev@local --name Dev --role admin
uvicorn app.main:app --reload
```

Em outro terminal, o worker:

```powershell
cd backend; .venv\Scripts\Activate.ps1; python -m app.worker
```

E o frontend:

```powershell
cd frontend
npm install
npm run dev     # http://localhost:5173, com proxy de /api para :8000
```

SQLite serve para o desenvolvimento; produção é Postgres. O OCR exige o
Tesseract instalado, o que na prática significa rodar dentro do contêiner.

## Testes

```powershell
cd backend
.venv\Scripts\Activate.ps1
pytest
```

A suíte não depende de PDFs externos: `tests/pdf_fixtures.py` gera os documentos
sinteticamente, inclusive variações com tabela sem grade e cabeçalho de duas
linhas, que são justamente os casos em que a extração costuma falhar.

## Operação

**Backup.** O que precisa de cópia é `/data/blobs` (originais, imutáveis) e o
banco. O `cache` é regenerável e fica fora do backup de propósito.

```bash
docker compose exec -T db pg_dump -U kr kr | gzip > backup-$(date +%F).sql.gz
docker run --rm -v kr-comparador_storage:/data -v "$PWD":/out alpine \
    tar czf /out/blobs-$(date +%F).tar.gz /data/blobs
```

Um backup nunca restaurado não é um backup: teste a restauração em uma cópia da
VM pelo menos uma vez por semestre.

**Atualização.** `git pull && docker compose up -d --build`, conferindo o
`/api/health` em seguida. O esquema é criado na subida; enquanto o produto for
de um único cliente, isso basta, mas assim que houver dados que não podem ser
recriados, migre para Alembic antes da primeira mudança destrutiva de esquema.

**Integridade dos arquivos.** `docker compose exec api python -m app.cli
verify-blobs` recalcula o hash de cada PDF armazenado e denuncia o que sumiu ou
corrompeu.

**Sessões.** `python -m app.cli purge-sessions`, em um cron semanal.

## Limites conhecidos

- Coordenadas dentro de desenhos vetoriais (planta em CAD sem tabela) não são
  extraídas: o sistema lê tabelas e texto, não interpreta o desenho.
- O OCR erra dígitos em documentos escaneados ruins. Por isso todo valor OCR
  entra com confiança reduzida e cai na tela de revisão.
- A camada de IA multimodal está prevista na configuração, mas desligada. Ela
  serve para casos difusos (achar a tabela em um layout estranho), nunca para
  produzir números — cálculo é código determinístico.
