import json
from datetime import date

from dedup import Processados, calcular_hash, deduplicar, mesmo_ato, similaridade
from modelos import Publicacao, normalizar_cnj


def pub(texto="Vistos. Manifeste-se o autor em 5 dias.", numero="1234567-89.2026.8.26.0100",
        d0="2026-09-16", fonte="DJEN", id_fonte="1", intimados=None):
    return Publicacao(fonte=fonte, id_fonte=id_fonte, tribunal="TJSP", orgao="1ª Vara",
                      numero_processo=normalizar_cnj(numero), numero_processo_original=numero,
                      partes=[], advogados=[], intimados=intimados or ["Danilo"],
                      data_disponibilizacao=d0, tipo_comunicacao="Intimação", tipo_documento="",
                      classe="", meio="D", link="", texto_integral=texto, texto_bruto=texto)


def test_normalizar_cnj():
    assert normalizar_cnj("12345678920268260100") == "1234567-89.2026.8.26.0100"
    assert normalizar_cnj("Processo 1234567-89.2026.8.26.0100 (principal)") == "1234567-89.2026.8.26.0100"
    assert normalizar_cnj("") is None
    assert normalizar_cnj("sem número") is None


def test_hash_estavel_e_insensivel_a_espacos_e_tags():
    a = calcular_hash("1234567-89.2026.8.26.0100", "2026-09-16", "Vistos.  Manifeste-se\n o autor")
    b = calcular_hash("1234567-89.2026.8.26.0100", "2026-09-16", "<p>Vistos. Manifeste-se o autor</p>")
    c = calcular_hash("1234567-89.2026.8.26.0100", "2026-09-17", "Vistos. Manifeste-se o autor")
    assert a == b and a != c


def test_deduplicar_mesma_intimacao_dois_advogados_e_duas_fontes():
    p1 = pub(intimados=["Danilo"], id_fonte="a")
    p2 = pub(intimados=["Pedro"], id_fonte="b")
    p3 = pub(fonte="DJE-TJSP", id_fonte="c", intimados=["Danilo"])
    out = deduplicar([p1, p2, p3])
    assert len(out) == 1
    assert sorted(out[0].intimados) == ["Danilo", "Pedro"]
    assert any("DJE-TJSP" in o for o in out[0].observacoes)


def test_processados_purga_e_republicacao(tmp_path):
    caminho = tmp_path / "processados.json"
    proc = Processados(caminho)
    antiga = pub(texto="Texto antigo qualquer", d0="2026-06-01")
    antiga.hash = calcular_hash(antiga.numero_processo, antiga.data_disponibilizacao, antiga.texto_integral)
    proc.registrar(antiga, hoje=date(2026, 6, 2))
    original = pub(texto="Vistos. Manifeste-se o autor em 5 dias sobre os documentos juntados.")
    original.hash = calcular_hash(original.numero_processo, original.data_disponibilizacao, original.texto_integral)
    proc.registrar(original, hoje=date(2026, 9, 16))
    proc.salvar()

    proc2 = Processados(caminho)
    proc2.purgar(hoje=date(2026, 9, 17))
    assert not proc2.contem(antiga.hash)          # mais de 60 dias
    assert proc2.contem(original.hash)

    repub = pub(texto="Vistos. Manifeste-se o autor em 5 dias sobre os documentos juntados. Int.",
                d0="2026-09-17")
    repub.hash = calcular_hash(repub.numero_processo, repub.data_disponibilizacao, repub.texto_integral)
    assert repub.hash != original.hash
    assert proc2.republicacao_de(repub, hoje=date(2026, 9, 17)) == original.hash

    diferente = pub(texto="Sentença. Julgo procedente o pedido para condenar a ré.", d0="2026-09-17")
    diferente.hash = calcular_hash(diferente.numero_processo, diferente.data_disponibilizacao, diferente.texto_integral)
    assert proc2.republicacao_de(diferente, hoje=date(2026, 9, 17)) is None

    outro_processo = pub(texto=original.texto_integral, numero="7654321-89.2026.8.26.0100", d0="2026-09-17")
    outro_processo.hash = calcular_hash(outro_processo.numero_processo, "2026-09-17", outro_processo.texto_integral)
    assert proc2.republicacao_de(outro_processo, hoje=date(2026, 9, 17)) is None


def test_similaridade_e_mesmo_ato():
    assert similaridade("abc", "abc") == 1.0
    assert similaridade("", "abc") == 0.0
    assert mesmo_ato("Mapfre X Siemens | Embargos de Declaração", "MAPFRE X SIEMENS | embargos de declaracao")
    assert not mesmo_ato("Mapfre X Siemens | Embargos de Declaração", "Mapfre X Siemens | Apelação")
    assert not mesmo_ato("sem barra", "sem barra")
