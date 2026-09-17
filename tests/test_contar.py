"""Testes obrigatórios do §3 do prompt de construção — calendário nacional de 2026 fixo."""
from datetime import date

import pytest

from contar import contar_prazo, lembrete, eh_dia_util

FERIADOS_2026 = {
    date(2026, 1, 1), date(2026, 2, 16), date(2026, 2, 17), date(2026, 4, 3), date(2026, 4, 21),
    date(2026, 5, 1), date(2026, 6, 4), date(2026, 9, 7), date(2026, 10, 12), date(2026, 11, 2),
    date(2026, 11, 15), date(2026, 11, 20), date(2026, 12, 25),
}


def test_1_d0_17_09_2026_15_e_5_dias_uteis():
    r15 = contar_prazo(date(2026, 9, 17), 15, FERIADOS_2026)
    assert r15.data_publicacao == date(2026, 9, 18)
    assert r15.dia_1 == date(2026, 9, 21)
    assert r15.data_final == date(2026, 10, 9)
    assert r15.feriados_no_intervalo == []
    r5 = contar_prazo(date(2026, 9, 17), 5, FERIADOS_2026)
    assert r5.data_final == date(2026, 9, 25)


def test_2_d0_31_07_2026_60_dias_uteis_com_feriados():
    # O cartão real do quadro marca 29/10; a regra do §4.1 dá 28/10. O teste documenta a
    # divergência (não ajustar o código para "bater" com o cartão sem descobrir o motivo).
    r = contar_prazo(date(2026, 7, 31), 60, FERIADOS_2026)
    assert r.data_publicacao == date(2026, 8, 3)
    assert r.dia_1 == date(2026, 8, 4)
    assert r.feriados_no_intervalo == [date(2026, 9, 7), date(2026, 10, 12)]
    assert r.data_final == date(2026, 10, 28)


def test_3_d0_sexta_publica_segunda_dia1_terca():
    r = contar_prazo(date(2026, 9, 18), 5, FERIADOS_2026)   # sexta
    assert r.data_publicacao == date(2026, 9, 21)           # segunda
    assert r.dia_1 == date(2026, 9, 22)                     # terça


def test_4_atravessa_recesso():
    r = contar_prazo(date(2026, 12, 15), 15, FERIADOS_2026)
    assert r.data_publicacao == date(2026, 12, 16)
    assert r.dia_1 == date(2026, 12, 17)
    assert r.atravessou_recesso is True
    assert r.data_final.year == 2027 and r.data_final.month == 2
    # 17 e 18/12 (dias 1-2), recesso 20/12–20/01, retoma 21/01 (dia 3) → dia 15 = 08/02/2027
    assert r.data_final == date(2027, 2, 8)


def test_5_final_em_sabado_prorroga_para_segunda():
    # Em dias úteis o final nunca cai em fim de semana; provoque com prazo em dias corridos.
    # D0 = quarta 16/09 → publ. 17/09 → dia 1 = 18/09 (sex) → 2 corridos = sáb 19/09 → seg 21/09.
    r = contar_prazo(date(2026, 9, 16), 2, FERIADOS_2026, dias_corridos=True)
    assert r.dia_1 == date(2026, 9, 18)
    assert r.data_final == date(2026, 9, 21)
    assert r.prorrogado is True


def test_6_dias_corridos_final_domingo_prorroga():
    # D0 = 17/09 (qui) → publ. 18/09 → dia 1 = 21/09 (seg) → 7 corridos = dom 27/09 → seg 28/09.
    r = contar_prazo(date(2026, 9, 17), 7, FERIADOS_2026, dias_corridos=True)
    assert r.data_final == date(2026, 9, 28)
    assert r.prorrogado is True


def test_d0_em_feriado_e_fim_de_semana():
    # D0 no feriado 07/09 (seg): publicação em 08/09, dia 1 em 09/09.
    r = contar_prazo(date(2026, 9, 7), 5, FERIADOS_2026)
    assert (r.data_publicacao, r.dia_1) == (date(2026, 9, 8), date(2026, 9, 9))


def test_suspensao_do_tribunal_nao_conta():
    susp = [(date(2026, 9, 22), date(2026, 9, 24))]
    r = contar_prazo(date(2026, 9, 17), 5, FERIADOS_2026, suspensoes=susp)
    # dia1 21/09, 22-24 suspensos, 25 (2), 28 (3), 29 (4), 30 (5)
    assert r.data_final == date(2026, 9, 30)
    assert any("suspensão" in o for o in r.observacoes)


def test_lembretes_paragrafo_4_4():
    r15 = contar_prazo(date(2026, 9, 17), 15, FERIADOS_2026)
    assert lembrete(r15.data_final, 15, FERIADOS_2026) == date(2026, 10, 2)     # 5º dia útil antes de 09/10
    r5 = contar_prazo(date(2026, 9, 17), 5, FERIADOS_2026)
    assert lembrete(r5.data_final, 5, FERIADOS_2026) == date(2026, 9, 23)       # 2º dia útil antes de 25/09
    r3 = contar_prazo(date(2026, 9, 17), 3, FERIADOS_2026)
    assert lembrete(r3.data_final, 3, FERIADOS_2026, dia_1=r3.dia_1) == date(2026, 9, 22)  # dia útil seguinte ao dia 1
    r60 = contar_prazo(date(2026, 7, 31), 60, FERIADOS_2026)
    assert lembrete(r60.data_final, 60, FERIADOS_2026) == date(2026, 10, 19)    # 7 dias úteis antes de 28/10
    # lembrete de prazo com feriado no caminho: fatal 15/10 (qui), 5 úteis antes pula 12/10
    assert lembrete(date(2026, 10, 15), 15, FERIADOS_2026) == date(2026, 10, 7)


def test_linha_contagem_mostra_a_conta():
    r = contar_prazo(date(2026, 7, 31), 60, FERIADOS_2026)
    assert r.linha_contagem() == "disp. 31/07 (sex) → publ. 03/08 (seg) → dia 1 = 04/08 (ter) → 60 dias úteis → 28/10/2026"


def test_eh_dia_util_recesso():
    assert not eh_dia_util(date(2026, 12, 21), set(), [(date(2026, 12, 20), date(2027, 1, 20))])
    assert eh_dia_util(date(2027, 1, 21), set(), [(date(2026, 12, 20), date(2027, 1, 20))])


def test_dias_invalido():
    with pytest.raises(ValueError):
        contar_prazo(date(2026, 9, 17), 0, set())
