"""Ciclo completo em dry-run com fonte simulada, estado em diretório temporário."""
import json
from pathlib import Path

import pytest

import rodar

MOCK = Path(__file__).parent / "fixtures" / "djen-mock.json"


@pytest.fixture
def ambiente(tmp_path, monkeypatch):
    estado, logs = tmp_path / "estado", tmp_path / "logs"
    estado.mkdir(); logs.mkdir()
    monkeypatch.setattr(rodar, "ESTADO", estado)
    monkeypatch.setattr(rodar, "LOGS", logs)
    for nome in ("EXEC", "PENDENTES", "CLASSIFICADAS", "LANCADOS", "ULTIMA", "PROCESSADOS"):
        monkeypatch.setattr(rodar, f"ARQ_{nome}", estado / getattr(rodar, f"ARQ_{nome}").name)
    monkeypatch.setenv("PRAZOS_DJEN_MOCK", str(MOCK))
    monkeypatch.delenv("TRELLO_KEY", raising=False)
    return estado, logs


def test_ciclo_dry_run(ambiente):
    estado, logs = ambiente
    assert rodar.main(["--etapa", "coletar", "--dry-run", "--data", "2026-09-10", "--ate", "2026-09-17"]) == 0
    pend = json.loads((estado / "pendentes.json").read_text(encoding="utf-8"))["publicacoes"]
    assert len(pend) == 3                                  # 4 itens da fonte, 2 são a mesma intimação
    assert sorted(p["intimados"] for p in pend if p["numero_processo"] == "0008819-53.2019.8.26.0100")[0] == ["Danilo", "Pedro"]
    assert (logs / "djen-2026-09-17.json").exists()

    # sem classificadas.json a etapa recusa
    assert rodar.main(["--etapa", "contar-e-lancar", "--dry-run"]) == 3

    por_num = {p["numero_processo"]: p["hash"] for p in pend}
    cls = [
        {"hash": por_num["0008819-53.2019.8.26.0100"], "categoria": "DECISAO_INTERLOCUTORIA",
         "prazos": [{"ato": "Embargos de Declaração", "dias": 5}, {"ato": "Agravo de Instrumento", "dias": 15}],
         "partes": {"autor": "Thiago Queiroz", "reu": "Dryclean"}},
        {"hash": por_num["0020672-15.2026.8.26.0100"], "categoria": "SENTENCA", "prazos": "inválido"},   # → INDETERMINADA
        # 1001234-56 fica sem classificação → INDETERMINADA
    ]
    (estado / "classificadas.json").write_text(json.dumps(cls), encoding="utf-8")
    assert rodar.main(["--etapa", "contar-e-lancar", "--dry-run"]) == 0
    ex = json.loads((estado / "execucao.json").read_text(encoding="utf-8"))
    prazos = ex["prazos"]
    assert len(prazos) == 6 and all(p["status"] == "DRY_RUN" for p in prazos)
    ed = next(p for p in prazos if p["ato"] == "Embargos de Declaração")
    assert (ed["d0"], ed["data_publicacao"], ed["dia_1"], ed["data_final"], ed["data_lembrete"]) == \
        ("2026-09-16", "2026-09-17", "2026-09-18", "2026-09-24", "2026-09-22")
    assert ed["titulo"] == "Thiago Queiroz X Dryclean | Embargos de Declaração"
    desc = ed["cartao_fatal"]["descricao"]
    assert desc.startswith("DJEN - TJSP\n\nDisponibilização: 16/09/2026\n\n")
    assert "Contagem: disp. 16/09 (qua) → publ. 17/09 (qui) → dia 1 = 18/09 (sex) → 5 dias úteis → 24/09/2026" in desc
    assert "Cartões desta publicação: Prazo Fatal 24/09 | Lembrete 22/09" in desc
    assert "Intimado: Danilo, Pedro" in desc
    texto_fonte = json.loads(MOCK.read_text(encoding="utf-8"))["items"][0]["texto"]
    assert "Vistos. A alegação de nulidade" in desc and "<p>" not in desc and "DETERMINO o regular prosseguimento" in texto_fonte
    indet = [p for p in prazos if p["categoria"] == "INDETERMINADA"]
    assert sorted(p["dias"] for p in indet) == [5, 5, 15, 15]
    assert all("DÚVIDA DE CLASSIFICAÇÃO" in p["etiquetas"] and "CONFERIR CONTAGEM" in p["etiquetas"] for p in indet)
    assert any("D0 ANTIGA" in a for a in ex["alertas"])
    assert sum("INDETERMINADA" in a for a in ex["alertas"]) >= 2

    rc = rodar.main(["--etapa", "autoverificar"])
    assert rc in (0, 1)   # reprova só se faltar ID de membro no config real
    assert rodar.main(["--etapa", "email", "--dry-run"]) == 0
    html = (logs / "email-2026-09-17.html").read_text(encoding="utf-8")
    assert "3 publicações · 6 prazos lançados" in html and "INDETERMINADA" in html
    assert (logs / "recortes-2026-09-17.txt").read_text(encoding="utf-8").count("Processo: ") == 3
    assert rodar.main(["--etapa", "fechar"]) == 1              # dry-run: não fecha
    assert rodar.main(["--etapa", "fechar", "--forcar"]) == 0
    proc = json.loads((estado / "processados.json").read_text(encoding="utf-8"))["itens"]
    assert len(proc) == 3
    # nova coleta da mesma janela: nada a classificar
    assert rodar.main(["--etapa", "coletar", "--dry-run", "--data", "2026-09-10", "--ate", "2026-09-17"]) == 0
    assert json.loads((estado / "pendentes.json").read_text(encoding="utf-8"))["publicacoes"] == []


def test_email_sem_publicacoes_e_erro_coleta(ambiente):
    estado, logs = ambiente
    assert rodar.main(["--etapa", "coletar", "--dry-run", "--data", "2026-01-05"]) == 0
    assert rodar.main(["--etapa", "contar-e-lancar", "--dry-run"]) == 0
    assert rodar.main(["--etapa", "email", "--dry-run"]) == 0
    txt = (logs / "email-2026-01-05.txt").read_text(encoding="utf-8")
    assert "0 publicações · 0 prazos lançados" in txt
    ex = json.loads((estado / "execucao.json").read_text(encoding="utf-8"))
    assert ex["email"]["assunto"] == "Publicações e prazos — 05/01/2026 — sem publicações"
    assert rodar.main(["--etapa", "email", "--dry-run", "--erro-coleta", "timeout DJEN"]) == 0
    ex = json.loads((estado / "execucao.json").read_text(encoding="utf-8"))
    assert ex["email"]["assunto"].startswith("[ATENÇÃO]") and "FALHA NA COLETA" in ex["email"]["assunto"]
    assert rodar.main(["--etapa", "fechar", "--forcar"]) == 1   # falha de coleta não avança a janela
