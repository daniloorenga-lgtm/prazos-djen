"""Legalcloud: lógica pura + driver Playwright de verdade contra uma calculadora falsa local (file://)."""
import json
import os
from datetime import date
from pathlib import Path

import pytest

from legalcloud import (ConferidorLegalcloud, ConferidorMock, ResultadoConferencia, extrair_data_resultado,
                        interpretar_simulacao, mensagem_de_aviso, resolver_conferencia, sigla_para_site)

FALSA = Path(__file__).parent / "fixtures" / "calculadora-falsa"


def test_extrair_data_resultado_prefere_palavra_chave():
    txt = "Publicado em 18/09/2026. Dia 1: 21/09/2026. Prazo final: 09/10/2026. Gerado em 17/09/2026."
    assert extrair_data_resultado(txt, apos=date(2026, 9, 18)) == date(2026, 10, 9)
    assert extrair_data_resultado("Vencimento 10/10/2026") == date(2026, 10, 10)
    assert extrair_data_resultado("sem data") is None
    assert extrair_data_resultado("31/02/2026 e 05/10/2026", apos=date(2026, 9, 1)) == date(2026, 10, 5)   # inválida ignorada


def test_interpretar_simulacao_real_do_site():
    txt = (Path(__file__).parent / "fixtures" / "legalcloud-simulacao.txt").read_text(encoding="utf-8")
    r = interpretar_simulacao(txt, 15)
    assert r["ok"] and r["data_final"] == date(2026, 10, 9) and r["dia_1"] == date(2026, 9, 21)
    assert r["dia_do_comeco"] == date(2026, 9, 18) and r["n_contados"] == 15 and r["desconsiderados"] == []
    assert interpretar_simulacao(txt, 10)["data_final"] == date(2026, 10, 2)      # pede o dia numerado == dias
    assert interpretar_simulacao("nada aqui", 5)["ok"] is False
    com_feriado = txt.replace("11\n05/10/2026 - Segunda-feira", "05/10/2026 -\nFeriado Municipal\n11\n06/10/2026 - Terça-feira")
    r2 = interpretar_simulacao(com_feriado, 15)
    assert r2["desconsiderados"] == [(date(2026, 10, 5), "Feriado Municipal")]


def test_mensagem_de_aviso_do_site():
    t = "Simular\n!\nQuantidade de dias inválida!\nPara simular prazos com data de evento 60 dias no futuro entre em contato conosco pelo chat.\nOK\n"
    assert mensagem_de_aviso(t).startswith("Quantidade de dias inválida! Para simular")
    assert mensagem_de_aviso("sem botão") == ""


def test_sigla_para_site():
    assert sigla_para_site("TRF3") == "TRF-3" and sigla_para_site("tjsp") == "TJSP" and sigla_para_site("TRF-1") == "TRF-1"


def test_resolver_conferencia_prevalece_a_mais_curta():
    local = date(2026, 10, 9)
    d, linha, div = resolver_conferencia(local, ResultadoConferencia(date(2026, 10, 9), True))
    assert d == local and "confere" in linha and not div
    d, linha, div = resolver_conferencia(local, ResultadoConferencia(date(2026, 10, 13), False))
    assert d == local and "DIVERGE" in linha and div and "13/10/2026" in linha
    d, linha, div = resolver_conferencia(local, ResultadoConferencia(date(2026, 10, 7), False))
    assert d == date(2026, 10, 7) and div
    d, linha, div = resolver_conferencia(local, ResultadoConferencia(None, None, "erro no site: x"))
    assert d == local and "não conferido" in linha and not div


def test_conferidor_mock(tmp_path):
    arq = tmp_path / "m.json"
    arq.write_text(json.dumps({"TJSP|2026-09-18|15|uteis": "2026-10-13", "*": "igual"}))
    with ConferidorMock(arq) as c:
        r = c.conferir("TJSP", "", date(2026, 9, 18), 15, False, date(2026, 10, 9))
        assert r.data_site == date(2026, 10, 13) and r.confere is False
        r = c.conferir("TJPR", "", date(2026, 9, 18), 5, False, date(2026, 9, 25))
        assert r.data_site == date(2026, 9, 25) and r.confere is True


@pytest.fixture
def cfg_falsa(tmp_path):
    pytest.importorskip("playwright")
    return {"url": FALSA.joinpath("calculadora.html").as_uri(), "url_login": FALSA.joinpath("login.html").as_uri(),
            "url_calculadora": FALSA.joinpath("calculadora.html").as_uri(), "timeout_segundos": 15,
            "sistema_padrao": "eSAJ", "campos_djen": {"sistema": "#sistema"}}


def _chromium(monkeypatch):
    exe = os.environ.get("PLAYWRIGHT_CHROMIUM_PATH")
    if not exe:
        for c in sorted(Path("/opt/pw-browsers").glob("chromium-*/chrome-linux/chrome")):
            exe = str(c)
    if exe:
        monkeypatch.setenv("PLAYWRIGHT_CHROMIUM_PATH", exe)


def test_driver_real_contra_calculadora_falsa(cfg_falsa, tmp_path, monkeypatch):
    _chromium(monkeypatch)
    monkeypatch.setenv("LEGALCLOUD_USER", "usuario@teste")
    monkeypatch.setenv("LEGALCLOUD_PASS", "segredo")
    with ConferidorLegalcloud(cfg_falsa, tmp_path) as c:
        assert c.logado
        # D0 17/09 (qui) → começo 18/09 → dia 1 21/09 → 15 úteis = 09/10 (igual à simulação real do site)
        r = c.conferir("TJSP", "", date(2026, 9, 17), 15, False, date(2026, 10, 9), fonte="DJEN")
        assert r.data_site == date(2026, 10, 9) and r.confere is True, r
        # D0 23/09 → começo 24/09 → dia 1 25/09 → 15 úteis: sem feriado = 15/10; o site falso tem 12/10 → 16/10 → divergência
        r = c.conferir("TJSP", "", date(2026, 9, 23), 15, False, date(2026, 10, 15))
        assert r.data_site == date(2026, 10, 16) and r.confere is False and "12/10" in r.observacao, r
        # dias corridos (Juizado): D0 17/09 → começo 18/09 → 7 corridos: 19..25/09 → 25/09 (sex)
        r = c.conferir("TJSP", "", date(2026, 9, 17), 7, True, date(2026, 9, 25))
        assert r.data_site == date(2026, 9, 25) and r.confere is True, r
        assert r.captura and Path(r.captura).exists()
        # TRF3 → "TRF-3" no site
        r = c.conferir("TRF3", "", date(2026, 9, 17), 5, False, date(2026, 9, 25))
        assert r.data_site == date(2026, 9, 25), r
        inv = json.loads(c.explorar().read_text(encoding="utf-8"))
        assert any(f["name"] == "tribunal" and f["options"] for f in inv["campos"])


def test_driver_login_recusado(cfg_falsa, tmp_path, monkeypatch):
    _chromium(monkeypatch)
    monkeypatch.setenv("LEGALCLOUD_USER", "usuario@teste")
    monkeypatch.setenv("LEGALCLOUD_PASS", "errada")
    with pytest.raises(RuntimeError, match="login"):
        with ConferidorLegalcloud(cfg_falsa, tmp_path):
            pass


def test_driver_campo_ausente_vira_resultado_nao_conferido(cfg_falsa, tmp_path, monkeypatch):
    _chromium(monkeypatch)
    monkeypatch.setenv("LEGALCLOUD_USER", "usuario@teste")
    monkeypatch.setenv("LEGALCLOUD_PASS", "segredo")
    cfg_falsa["campos_djen"] = {"dias": "#nao-existe"}
    cfg_falsa["timeout_segundos"] = 3
    with ConferidorLegalcloud(cfg_falsa, tmp_path) as c:
        r = c.conferir("TJSP", "", date(2026, 9, 17), 15, False, date(2026, 10, 9))
        assert r.data_site is None and r.confere is None and "erro no site" in r.observacao
        # sem sistema selecionável para o TJSP o site recusa; o driver relata em vez de inventar data
        cfg_falsa["campos_djen"] = {"sistema": "#nao-existe"}
        r = c.conferir("TJSP", "", date(2026, 9, 17), 15, False, date(2026, 10, 9))
        assert r.data_site is None and "sistema" in r.observacao
