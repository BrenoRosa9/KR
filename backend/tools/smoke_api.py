"""Exercita a aplicação inteira por HTTP, com dois PDFs de verdade.

Sobe nada por conta própria: espera a API em ``--base`` e um worker rodando.
Envia o par, acompanha a fila, imprime o resumo e grava o laudo.

    python tools/smoke_api.py A.pdf B.pdf
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import httpx


def main() -> int:
    parser = argparse.ArgumentParser(prog="smoke_api")
    parser.add_argument("pdf_a")
    parser.add_argument("pdf_b")
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--saida", default="laudo.html")
    args = parser.parse_args()

    with httpx.Client(base_url=args.base, timeout=120.0) as client:
        pdf_a, pdf_b = Path(args.pdf_a), Path(args.pdf_b)
        files = {
            "file_a": (pdf_a.name, pdf_a.read_bytes(), "application/pdf"),
            "file_b": (pdf_b.name, pdf_b.read_bytes(), "application/pdf"),
        }
        response = client.post(
            "/api/analyses/upload",
            files=files,
            params={"profile": "padrao", "title": "Verificação com documentos reais"},
        )
        response.raise_for_status()
        analysis_id = response.json()["id"]
        print(f"Análise {analysis_id} criada. Aguardando o worker…")

        deadline = time.monotonic() + args.timeout
        detail = None
        while time.monotonic() < deadline:
            detail = client.get(f"/api/analyses/{analysis_id}").json()
            status = detail["analysis"]["status"]
            if status not in {"pending", "extracting"}:
                break
            time.sleep(2.0)
        else:
            print("A análise não terminou no tempo previsto.")
            return 1

        analysis = detail["analysis"]
        print(f"Situação: {analysis['status']}")
        print(f"Resumo: {analysis['summary']}")
        if analysis["error"]:
            print(f"Erro: {analysis['error']}")

        for extraction in detail["extractions"]:
            print(
                f"  {extraction['role']}: {extraction['crs_epsg']} "
                f"({extraction['datum_label']} fuso {extraction['utm_zone']}), "
                f"tabela via {extraction['table_strategy']}"
            )
            for warning in extraction["warnings"] or []:
                print(f"     aviso: {warning}")

        errors = [f for f in detail["findings"] if f["severity"] == "error"]
        print(f"\n{len(errors)} achado(s) de severidade máxima:")
        for finding in errors[:15]:
            print(f"  [{finding['kind']}] {finding['subject']}: {finding['message']}")

        observations = client.get(f"/api/analyses/{analysis_id}/observations").json()
        print(f"\n{len(observations)} observações gravadas para revisão.")
        located = sum(1 for item in observations if item["bbox"])
        print(f"{located} delas com região destacável no PDF.")

        report = client.get(f"/api/analyses/{analysis_id}/report.html")
        if report.status_code == 200:
            Path(args.saida).write_text(report.text, encoding="utf-8")
            print(f"\nLaudo salvo em {args.saida} ({len(report.text)} caracteres).")
        else:
            print(f"\nLaudo indisponível: {report.status_code} {report.text[:200]}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
