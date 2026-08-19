"""Teste de integração do fluxo completo, do upload ao laudo.

Roda sobre SQLite e um diretório temporário: o objetivo é provar que as peças se
encaixam — armazenamento, fila, extração, comparação, revisão e laudo — sem
depender de Postgres nem de Docker. As diferenças de dialeto que importam
(``JSONB``, ``SKIP LOCKED``) estão isoladas em variantes no código, justamente
para que este teste seja possível.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC

import pytest

pytest.importorskip("fastapi")


@pytest.fixture
def api(tmp_path, monkeypatch):
    """Aplicação configurada sobre SQLite, com módulos recarregados."""
    import importlib
    import sys

    database_path = tmp_path / "test.db"
    monkeypatch.setenv("KR_DATABASE_URL", f"sqlite+pysqlite:///{database_path}")
    monkeypatch.setenv("KR_STORAGE_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("KR_ENVIRONMENT", "test")
    monkeypatch.setenv("KR_OCR_ENABLED", "false")

    # A configuração é memoizada e o engine é criado na importação do módulo de
    # banco; recarregar é o que permite apontar para o SQLite deste teste.
    for name in [
        "app.config",
        "app.db",
        "app.storage",
        "app.jobs",
        "app.repository",
        "app.services",
        "app.security",
        "app.deps",
        "app.api.documents",
        "app.api.analyses",
        "app.api.reports",
        "app.main",
    ]:
        if name in sys.modules:
            importlib.reload(sys.modules[name])
        else:
            importlib.import_module(name)

    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.db import create_all
    from app.main import create_app

    get_settings.cache_clear()
    settings = get_settings()
    settings.ensure_directories()
    create_all()

    client = TestClient(create_app())
    yield client
    client.close()


@pytest.fixture
def pdfs(tmp_path):
    from pdf_fixtures import MemorialSpec, write_memorial

    ring = [
        (333_000.000, 7_394_000.000),
        (333_120.500, 7_394_010.250),
        (333_135.750, 7_394_130.900),
        (333_010.250, 7_394_115.400),
    ]
    moved = list(ring)
    moved[2] = (moved[2][0] + 0.90, moved[2][1])

    return {
        "a": write_memorial(tmp_path / "doc_a.pdf", MemorialSpec(ring=ring)),
        "b": write_memorial(tmp_path / "doc_b.pdf", MemorialSpec(ring=moved)),
        "identical": write_memorial(tmp_path / "doc_c.pdf", MemorialSpec(ring=ring)),
    }


def drain_queue() -> int:
    """Executa a fila de forma síncrona, como o worker faria."""
    from app.db import session_scope
    from app.jobs import claim, finish
    from app.worker import run_job

    processed = 0
    while True:
        with session_scope() as session:
            job = claim(session)
            if job is None:
                return processed
            detached = type(
                "J",
                (),
                {"id": job.id, "kind": job.kind, "payload": dict(job.payload)},
            )()

        run_job(detached)
        with session_scope() as session:
            from app.models import Job

            stored = session.get(Job, detached.id)
            if stored is not None:
                finish(session, stored)
        processed += 1


class TestOpenAccess:
    def test_analyses_are_reachable_without_login(self, api):
        assert api.get("/api/analyses").status_code == 200


class TestUploadValidation:
    def test_non_pdf_is_rejected_by_content_not_extension(self, api):
        response = api.post(
            "/api/documents",
            files={
                "file": ("malicioso.pdf", b"MZ\x90\x00 executavel", "application/pdf")
            },
        )
        assert response.status_code == 400
        assert "não é um PDF" in response.json()["detail"]

    def test_identical_content_is_deduplicated(self, api, pdfs):
        first = api.post(
            "/api/documents",
            files={"file": ("a.pdf", pdfs["a"].read_bytes(), "application/pdf")},
        )
        second = api.post(
            "/api/documents",
            files={"file": ("copia.pdf", pdfs["a"].read_bytes(), "application/pdf")},
        )
        assert first.status_code in {200, 201}
        assert second.json()["id"] == first.json()["id"]

    def test_comparing_a_document_with_itself_is_refused(self, api, pdfs):
        uploaded = api.post(
            "/api/documents",
            files={"file": ("a.pdf", pdfs["a"].read_bytes(), "application/pdf")},
        ).json()
        response = api.post(
            "/api/analyses",
            json={
                "document_a_id": uploaded["id"],
                "document_b_id": uploaded["id"],
                "profile": "padrao",
            },
        )
        assert response.status_code == 400
        assert "mesmo arquivo" in response.json()["detail"]


class TestFullFlow:
    def test_upload_pair_creates_analysis_and_queues_work(self, api, pdfs):
        response = api.post(
            "/api/analyses/upload",
            files={
                "file_a": ("a.pdf", pdfs["a"].read_bytes(), "application/pdf"),
                "file_b": ("b.pdf", pdfs["b"].read_bytes(), "application/pdf"),
            },
        )
        assert response.status_code == 201, response.text
        assert response.json()["status"] == "pending"

        # A requisição respondeu sem processar nada: o trabalho está na fila.
        assert drain_queue() >= 1

    def test_divergence_is_found_and_traceable(self, api, pdfs):
        created = api.post(
            "/api/analyses/upload",
            files={
                "file_a": ("a.pdf", pdfs["a"].read_bytes(), "application/pdf"),
                "file_b": ("b.pdf", pdfs["b"].read_bytes(), "application/pdf"),
            },
        ).json()
        drain_queue()

        detail = api.get(f"/api/analyses/{created['id']}").json()
        assert detail["analysis"]["status"] in {"compared", "awaiting_review"}
        assert detail["analysis"]["summary"]["erros"] >= 1

        errors = [f for f in detail["findings"] if f["severity"] == "error"]
        coordinate = [f for f in errors if f["field"] in {"easting", "northing"}]
        assert coordinate, [f["message"] for f in errors]

        # Rastreabilidade: página e região de origem nos dois documentos.
        provenance = coordinate[0]["provenance_a"]
        assert provenance["page"] == 1
        assert provenance["bbox"] is not None
        assert provenance["raw_text"]

    def test_identical_documents_produce_no_errors(self, api, pdfs):
        created = api.post(
            "/api/analyses/upload",
            files={
                "file_a": ("a.pdf", pdfs["a"].read_bytes(), "application/pdf"),
                "file_b": ("c.pdf", pdfs["identical"].read_bytes(), "application/pdf"),
            },
        ).json()
        drain_queue()

        detail = api.get(f"/api/analyses/{created['id']}").json()
        errors = [f for f in detail["findings"] if f["severity"] == "error"]
        assert errors == [], [f["message"] for f in errors]

    def test_crs_is_resolved_from_the_documents(self, api, pdfs):
        created = api.post(
            "/api/analyses/upload",
            files={
                "file_a": ("a.pdf", pdfs["a"].read_bytes(), "application/pdf"),
                "file_b": ("b.pdf", pdfs["b"].read_bytes(), "application/pdf"),
            },
        ).json()
        drain_queue()

        detail = api.get(f"/api/analyses/{created['id']}").json()
        for extraction in detail["extractions"]:
            assert extraction["crs_epsg"] == "EPSG:31983"
            assert extraction["utm_zone"] == 23

    def test_stage_log_is_visible_for_diagnosis(self, api, pdfs):
        created = api.post(
            "/api/analyses/upload",
            files={
                "file_a": ("a.pdf", pdfs["a"].read_bytes(), "application/pdf"),
                "file_b": ("b.pdf", pdfs["b"].read_bytes(), "application/pdf"),
            },
        ).json()
        drain_queue()

        detail = api.get(f"/api/analyses/{created['id']}").json()
        stages = {s["stage"]: s for s in detail["extractions"][0]["stages"]}
        assert stages["triage"]["ok"]
        assert stages["parcel"]["ok"]

    def test_pdf_is_served_back_for_the_viewer(self, api, pdfs):
        created = api.post(
            "/api/analyses/upload",
            files={
                "file_a": ("a.pdf", pdfs["a"].read_bytes(), "application/pdf"),
                "file_b": ("b.pdf", pdfs["b"].read_bytes(), "application/pdf"),
            },
        ).json()
        detail = api.get(f"/api/analyses/{created['id']}").json()
        document_id = detail["documents"]["A"]["id"]

        response = api.get(f"/api/documents/{document_id}/file")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
        assert response.content.startswith(b"%PDF-")


class TestReview:
    def test_observations_are_listed_with_provenance(self, api, pdfs):
        created = api.post(
            "/api/analyses/upload",
            files={
                "file_a": ("a.pdf", pdfs["a"].read_bytes(), "application/pdf"),
                "file_b": ("b.pdf", pdfs["b"].read_bytes(), "application/pdf"),
            },
        ).json()
        drain_queue()

        observations = api.get(
            f"/api/analyses/{created['id']}/observations", params={"role": "A"}
        ).json()
        assert observations
        eastings = [o for o in observations if o["field"] == "easting"]
        assert len(eastings) == 4
        assert all(o["bbox"] is not None for o in eastings)
        assert all(o["raw_text"] for o in eastings)

    def test_human_correction_changes_the_outcome(self, api, pdfs):
        """O ciclo que fecha o produto: corrigir um valor e recomparar.

        A divergência introduzida em B é uma coordenada 0,90 m diferente. Ao
        corrigir a observação de B para o valor de A, o erro tem que desaparecer
        do relatório sem que nada seja reextraído.
        """
        created = api.post(
            "/api/analyses/upload",
            files={
                "file_a": ("a.pdf", pdfs["a"].read_bytes(), "application/pdf"),
                "file_b": ("b.pdf", pdfs["b"].read_bytes(), "application/pdf"),
            },
        ).json()
        drain_queue()

        before = api.get(f"/api/analyses/{created['id']}").json()
        assert before["analysis"]["summary"]["erros"] >= 1

        observations_b = api.get(
            f"/api/analyses/{created['id']}/observations", params={"role": "B"}
        ).json()
        target = next(
            o
            for o in observations_b
            if o["field"] == "easting" and o["vertex_index"] == 2
        )

        observations_a = api.get(
            f"/api/analyses/{created['id']}/observations", params={"role": "A"}
        ).json()
        reference = next(
            o
            for o in observations_a
            if o["field"] == "easting" and o["vertex_index"] == 2
        )

        patched = api.patch(
            f"/api/observations/{target['id']}",
            json={"value_num": reference["value_num"], "recompare": True},
        )
        assert patched.status_code == 200
        body = patched.json()
        assert body["edited"] is True
        # O valor lido no documento é preservado ao lado do corrigido.
        assert body["original_value_num"] == pytest.approx(target["value_num"])

        drain_queue()
        after = api.get(f"/api/analyses/{created['id']}").json()
        coordinate_errors = [
            f
            for f in after["findings"]
            if f["severity"] == "error" and f["field"] in {"easting", "northing"}
        ]
        assert coordinate_errors == [], [f["message"] for f in coordinate_errors]

    def test_recompare_keeps_exact_equality(self, api, pdfs):
        created = api.post(
            "/api/analyses/upload",
            files={
                "file_a": ("a.pdf", pdfs["a"].read_bytes(), "application/pdf"),
                "file_b": ("c.pdf", pdfs["identical"].read_bytes(), "application/pdf"),
            },
        ).json()
        drain_queue()

        response = api.post(
            f"/api/analyses/{created['id']}/recompare", params={"profile": "exato"}
        )
        assert response.status_code == 200
        drain_queue()

        detail = api.get(f"/api/analyses/{created['id']}").json()
        assert detail["analysis"]["profile_name"] == "exato"

    def test_unknown_profile_is_refused(self, api, pdfs):
        created = api.post(
            "/api/analyses/upload",
            files={
                "file_a": ("a.pdf", pdfs["a"].read_bytes(), "application/pdf"),
                "file_b": ("b.pdf", pdfs["b"].read_bytes(), "application/pdf"),
            },
        ).json()
        response = api.post(
            f"/api/analyses/{created['id']}/recompare", params={"profile": "inventado"}
        )
        assert response.status_code == 400

    def test_manual_crs_confirmation(self, api, pdfs):
        created = api.post(
            "/api/analyses/upload",
            files={
                "file_a": ("a.pdf", pdfs["a"].read_bytes(), "application/pdf"),
                "file_b": ("b.pdf", pdfs["b"].read_bytes(), "application/pdf"),
            },
        ).json()
        drain_queue()

        detail = api.get(f"/api/analyses/{created['id']}").json()
        extraction_id = detail["extractions"][0]["id"]

        response = api.put(
            f"/api/extractions/{extraction_id}/crs",
            json={
                "epsg": "EPSG:29193",
                "datum_label": "SAD69",
                "utm_zone": 23,
                "hemisphere": "S",
                "average_height_m": 750.0,
            },
        )
        assert response.status_code == 200
        assert response.json()["crs_epsg"] == "EPSG:29193"
        assert response.json()["average_height_m"] == pytest.approx(750.0)


class TestReport:
    def test_html_report_contains_the_findings_and_exact_criterion(self, api, pdfs):
        created = api.post(
            "/api/analyses/upload",
            files={
                "file_a": ("a.pdf", pdfs["a"].read_bytes(), "application/pdf"),
                "file_b": ("b.pdf", pdfs["b"].read_bytes(), "application/pdf"),
            },
        ).json()
        drain_queue()

        response = api.get(f"/api/analyses/{created['id']}/report.html")
        assert response.status_code == 200
        html = response.text
        assert "Laudo de conferência" in html
        assert "igualdade exata" in html.lower() or "Critério de comparação" in html
        # O laudo tem que mostrar a origem dos valores divergentes.
        assert "p. 1" in html
        assert "Fator combinado de escala" in html

    def test_report_before_comparison_is_refused(self, api, pdfs):
        created = api.post(
            "/api/analyses/upload",
            files={
                "file_a": ("a.pdf", pdfs["a"].read_bytes(), "application/pdf"),
                "file_b": ("b.pdf", pdfs["b"].read_bytes(), "application/pdf"),
            },
        ).json()
        response = api.get(f"/api/analyses/{created['id']}/report.html")
        assert response.status_code == 409

    def test_report_records_human_corrections(self, api, pdfs):
        created = api.post(
            "/api/analyses/upload",
            files={
                "file_a": ("a.pdf", pdfs["a"].read_bytes(), "application/pdf"),
                "file_b": ("b.pdf", pdfs["b"].read_bytes(), "application/pdf"),
            },
        ).json()
        drain_queue()

        observations = api.get(
            f"/api/analyses/{created['id']}/observations", params={"role": "B"}
        ).json()
        target = next(o for o in observations if o["field"] == "easting")
        api.patch(
            f"/api/observations/{target['id']}",
            json={"value_num": target["value_num"], "recompare": False},
        )

        html = api.get(f"/api/analyses/{created['id']}/report.html").text
        assert "Correções aplicadas por revisão humana" in html
        assert "—" in html


class TestOperational:
    def test_health_reports_queue_depth(self, api):
        response = api.get("/api/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["database"] is True
        assert "queued" in body["queue"]

    def test_history_lists_analyses_newest_first(self, api, pdfs):
        for _ in range(2):
            api.post(
                "/api/analyses/upload",
                files={
                    "file_a": ("a.pdf", pdfs["a"].read_bytes(), "application/pdf"),
                    "file_b": ("b.pdf", pdfs["b"].read_bytes(), "application/pdf"),
                },
            )
        history = api.get("/api/analyses").json()
        assert len(history) >= 1
        assert history[0]["created_at"] >= history[-1]["created_at"]

    def test_profiles_are_exposed_to_the_interface(self, api):
        profiles = api.get("/api/profiles").json()
        assert len(profiles) == 1
        assert profiles[0]["key"] == "exato"
        assert profiles[0]["coordinate_m"] == 0.0
        assert profiles[0]["distance_m"] == 0.0

    def test_missing_analysis_is_404(self, api):
        assert api.get(f"/api/analyses/{uuid.uuid4()}").status_code == 404

    def test_stale_job_is_returned_to_the_queue(self, api):
        """Worker morto no meio do trabalho não deve travar a análise."""
        from datetime import datetime, timedelta

        from app.db import session_scope
        from app.jobs import enqueue, requeue_stale
        from app.models import Job, JobStatus

        with session_scope() as session:
            job = enqueue(session, "recompare", {"analysis_id": str(uuid.uuid4())})
            job.status = JobStatus.RUNNING
            job.attempts = 1
            job.locked_at = datetime.now(UTC) - timedelta(hours=2)
            job_id = job.id

        with session_scope() as session:
            assert requeue_stale(session, timeout_seconds=60) == 1
            assert session.get(Job, job_id).status == JobStatus.QUEUED


def test_storage_layout_keeps_originals_immutable(api, pdfs, tmp_path):
    """Blobs por hash, derivados separados: é o que torna o backup simples."""
    api.post(
        "/api/documents",
        files={"file": ("a.pdf", pdfs["a"].read_bytes(), "application/pdf")},
    )

    blobs = tmp_path / "data" / "blobs"
    stored = list(blobs.rglob("*"))
    files = [path for path in stored if path.is_file()]
    assert len(files) == 1
    # Nome é o hash, e o diretório é o prefixo de dois caracteres.
    assert files[0].name == files[0].parent.name + files[0].name[2:]
    assert (tmp_path / "data" / "cache").exists()
    assert os.path.getsize(files[0]) > 0
