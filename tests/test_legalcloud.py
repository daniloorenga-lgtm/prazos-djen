"""Legalcloud: lógica pura + driver Playwright de verdade contra uma calculadora falsa local (file://)."""
import json
import os
from datetime import date
from pathlib import Path

import pytest

from legalcloud import (ConferidorLegalcloud, ConferidorMock, ResultadoConferencia, extrair_data_resultado,
                        resolver_conferencia)

FALSA = Path(__file__).parent / "fixtures" / "calculadora-falsa"


def test_extrair_data_resultado_prefere_palavra_chave():
    txt = "Publicado em 18/09/2026. Dia 1: 21/09/2026. Prazo final: 09/10/2026. Gerado em 17/09/2026."
    assert extrair_data_resultado(txt, apos=date(2026, 9, 18)) == date(2026, 10, 9)
    assert extrair_data_resultado("Vencimento 10/10/2026") == date(2026, 10, 10)
    assert extrair_data_resultado("sem data") is None
    assert extrair_data_resultado("31/02/2026 e 05/10/2026", apos=date(2026, 9, 1)) == date(2026, 10, 5)   # inválida ignorada


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
            "tribunais_nomes": {"TJSP": "TJSP"}}


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
        # publicação 18/09 (sex) → dia 1 21/09 → 15 úteis = 09/10 (o site falso não tem feriado nesse intervalo)
        r = c.conferir("TJSP", "Cível", date(2026, 9, 18), 15, False, date(2026, 10, 9))
        assert r.data_site == date(2026, 10, 9) and r.confere is True, r
        # publicação 24/09 → dia 1 25/09 → 15 úteis: sem feriado = 15/10; o site falso tem 12/10 → 16/10 → divergência
        r = c.conferir("TJSP", "Cível", date(2026, 9, 24), 15, False, date(2026, 10, 15))
        assert r.data_site == date(2026, 10, 16) and r.confere is False, r
        # dias corridos: 18/09 → dia 1 21/09 → 7 corridos = 27/09 (dom) → prorroga 28/09
        r = c.conferir("TJSP", "Cível", date(2026, 9, 18), 7, True, date(2026, 9, 28))
        assert r.data_site == date(2026, 9, 28) and r.confere is True, r
        assert r.captura and Path(r.captura).exists()
        inv = json.loads(c.explorar().read_text(encoding="utf-8"))
        assert any(f["label"] == "Tribunal" and f["options"] for f in inv["campos"])


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
    cfg_falsa["campos"] = {"dias": ["css=#nao-existe"]}
    with ConferidorLegalcloud(cfg_falsa, tmp_path) as c:
        r = c.conferir("TJSP", "", date(2026, 9, 18), 15, False, date(2026, 10, 9))
        assert r.data_site is None and r.confere is None and "campo 'dias'" in r.observacao
