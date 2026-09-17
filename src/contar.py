"""Contagem de prazos processuais — arts. 219, 224 e 231 do CPC (§4 da especificação).

Regra:
  D0 (disponibilização) → publicação = 1º dia útil após D0 (art. 224 §2º)
  → dia 1 = 1º dia útil após a publicação (art. 224 §3º)
  → conta dias úteis (ou corridos), dies ad quem incluído
  → final em dia não útil prorroga para o próximo dia útil (art. 224 §1º).
Recesso 20/12–20/01 (art. 220): suspende — os dias do intervalo não contam, retoma em 21/01.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from calendario import recessos


@dataclass
class Resultado:
    disponibilizacao: date
    data_publicacao: date
    dia_1: date
    data_final: date
    dias: int
    dias_corridos: bool
    feriados_no_intervalo: list[date] = field(default_factory=list)
    atravessou_recesso: bool = False
    prorrogado: bool = False
    observacoes: list[str] = field(default_factory=list)

    def linha_contagem(self) -> str:
        """Texto da linha 'Contagem:' do cartão (§5.3)."""
        def f(d: date) -> str:
            return f"{d.strftime('%d/%m')} ({DIA_SEMANA[d.weekday()]})"
        tipo = "dias corridos" if self.dias_corridos else "dias úteis"
        s = (f"disp. {f(self.disponibilizacao)} → publ. {f(self.data_publicacao)} → "
             f"dia 1 = {f(self.dia_1)} → {self.dias} {tipo} → {self.data_final.strftime('%d/%m/%Y')}")
        if self.prorrogado:
            s += " (prorrogado: final caiu em dia não útil)"
        return s


DIA_SEMANA = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]


def _em_suspensao(d: date, suspensoes: list[tuple[date, date]]) -> bool:
    return any(ini <= d <= fim for ini, fim in suspensoes)


def eh_dia_util(d: date, feriados: set[date], suspensoes: list[tuple[date, date]]) -> bool:
    if d.weekday() >= 5:
        return False
    if d in feriados:
        return False
    if _em_suspensao(d, suspensoes):
        return False
    return True


def proximo_dia_util(d: date, feriados: set[date], suspensoes: list[tuple[date, date]],
                     incluir_hoje: bool = False) -> date:
    x = d if incluir_hoje else d + timedelta(days=1)
    guarda = 0
    while not eh_dia_util(x, feriados, suspensoes):
        x += timedelta(days=1)
        guarda += 1
        if guarda > 400:
            raise RuntimeError("calendário sem dia útil em mais de 400 dias — verifique as suspensões")
    return x


def _suspensoes_com_recesso(disponibilizacao: date, suspensoes: list[tuple[date, date]] | None) -> list[tuple[date, date]]:
    """Garante o recesso legal mesmo que o chamador não o tenha passado."""
    out = list(suspensoes or [])
    anos = [disponibilizacao.year - 1, disponibilizacao.year, disponibilizacao.year + 1]
    for r in recessos(anos):
        if r not in out:
            out.append(r)
    return out


def contar_prazo(disponibilizacao: date, dias: int, feriados: set[date],
                 suspensoes: list[tuple[date, date]] | None = None,
                 dias_corridos: bool = False) -> Resultado:
    if dias <= 0:
        raise ValueError("dias deve ser positivo")
    susp = _suspensoes_com_recesso(disponibilizacao, suspensoes)
    feriados = set(feriados or set())
    obs: list[str] = []

    publicacao = proximo_dia_util(disponibilizacao, feriados, susp)
    dia_1 = proximo_dia_util(publicacao, feriados, susp)

    prorrogado = False
    if dias_corridos:
        final = dia_1 + timedelta(days=dias - 1)
        if not eh_dia_util(final, feriados, susp):
            final = proximo_dia_util(final, feriados, susp)
            prorrogado = True
        obs.append("contagem em dias corridos (Juizados / penal) — o recesso NÃO foi aplicado; conferir")
    else:
        contados = 1
        final = dia_1
        while contados < dias:
            final = proximo_dia_util(final, feriados, susp)
            contados += 1

    inicio_intervalo = publicacao
    feriados_intervalo = sorted(d for d in feriados
                                if inicio_intervalo <= d <= final and d.weekday() < 5)
    atravessou = any(ini <= final and fim >= inicio_intervalo for ini, fim in recessos(
        [disponibilizacao.year - 1, disponibilizacao.year, disponibilizacao.year + 1]))
    outras_susp = [(i, f) for i, f in susp if (i, f) not in recessos(
        [disponibilizacao.year - 1, disponibilizacao.year, disponibilizacao.year + 1])]
    for i, f in outras_susp:
        if i <= final and f >= inicio_intervalo:
            obs.append(f"suspensão de prazo no intervalo: {i.strftime('%d/%m')}–{f.strftime('%d/%m')}")
    if atravessou and not dias_corridos:
        obs.append("prazo atravessou o recesso forense (20/12–20/01): contagem suspensa, retomada em 21/01")

    return Resultado(
        disponibilizacao=disponibilizacao, data_publicacao=publicacao, dia_1=dia_1,
        data_final=final, dias=dias, dias_corridos=dias_corridos,
        feriados_no_intervalo=feriados_intervalo, atravessou_recesso=atravessou,
        prorrogado=prorrogado, observacoes=obs,
    )


def dias_uteis_antes(d: date, n: int, feriados: set[date], suspensoes: list[tuple[date, date]]) -> date:
    x = d
    for _ in range(n):
        x -= timedelta(days=1)
        while not eh_dia_util(x, feriados, suspensoes):
            x -= timedelta(days=1)
    return x


def lembrete(data_final: date, dias_prazo: int, feriados: set[date] | None = None,
             suspensoes: list[tuple[date, date]] | None = None,
             dia_1: date | None = None) -> date:
    """§4.4: 15 dias → 5º dia útil antes; 5 dias → 2º dia útil antes;
    ≤3 dias → dia útil seguinte ao dia 1; >15 → 7 dias úteis antes."""
    feriados = set(feriados or set())
    susp = _suspensoes_com_recesso(data_final, suspensoes)
    if dias_prazo <= 3:
        base = dia_1 or data_final
        if dia_1 is None:
            return dias_uteis_antes(data_final, 1, feriados, susp) if data_final > date.min else data_final
        prox = proximo_dia_util(base, feriados, susp)
        return min(prox, data_final)
    if dias_prazo > 15:
        return dias_uteis_antes(data_final, 7, feriados, susp)
    if dias_prazo >= 10:
        return dias_uteis_antes(data_final, 5, feriados, susp)
    return dias_uteis_antes(data_final, 2, feriados, susp)
