"""Valida dados/feriados-forenses.json (usado pela rotina semanal do Legalcloud).

Uso: python src/validar_calendario.py dados/feriados-forenses.json
Sai com código 0 se válido; 1 e mensagens caso contrário.
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path

FERIADOS_NACIONAIS_FIXOS = {(1, 1): "Confraternização Universal", (4, 21): "Tiradentes",
                            (5, 1): "Dia do Trabalho", (9, 7): "Independência",
                            (10, 12): "Nossa Senhora Aparecida", (11, 2): "Finados",
                            (11, 15): "Proclamação da República", (12, 25): "Natal"}


def _data(s):
    return date.fromisoformat(str(s)[:10])


def validar(caminho: Path) -> list[str]:
    erros: list[str] = []
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return [f"JSON inválido: {e}"]
    if not isinstance(dados, dict):
        return ["raiz deve ser objeto"]
    for campo in ("extraido_em", "fonte", "tribunais"):
        if campo not in dados:
            erros.append(f"campo obrigatório ausente: {campo}")
    try:
        datetime.fromisoformat(dados.get("extraido_em", ""))
    except ValueError:
        erros.append("extraido_em não é data/hora ISO 8601")
    horizonte = None
    if dados.get("horizonte_ate"):
        try:
            horizonte = _data(dados["horizonte_ate"])
        except ValueError:
            erros.append("horizonte_ate inválido")

    todas_datas: set[date] = set()

    def checar_lista(lista, contexto, chave="data"):
        vistos = set()
        for i, item in enumerate(lista or []):
            if not isinstance(item, dict) or chave not in item:
                erros.append(f"{contexto}[{i}] sem campo '{chave}'")
                continue
            try:
                d = _data(item[chave])
            except ValueError:
                erros.append(f"{contexto}[{i}] data inválida: {item[chave]!r}")
                continue
            if d in vistos:
                erros.append(f"{contexto}: data duplicada {d.isoformat()}")
            vistos.add(d)
            todas_datas.add(d)
            if not item.get("descricao"):
                erros.append(f"{contexto}[{i}] ({d}) sem descricao")

    checar_lista(dados.get("feriados_nacionais", []), "feriados_nacionais")
    tribunais = dados.get("tribunais", {})
    if not isinstance(tribunais, dict):
        erros.append("tribunais deve ser objeto")
        tribunais = {}
    for sigla, t in tribunais.items():
        if not isinstance(t, dict):
            erros.append(f"tribunais.{sigla} deve ser objeto")
            continue
        checar_lista(t.get("feriados", []), f"tribunais.{sigla}.feriados")
        for i, s in enumerate(t.get("suspensoes", []) or []):
            try:
                ini, fim = _data(s["inicio"]), _data(s["fim"])
                if fim < ini:
                    erros.append(f"tribunais.{sigla}.suspensoes[{i}] fim < inicio")
            except (KeyError, ValueError, TypeError):
                erros.append(f"tribunais.{sigla}.suspensoes[{i}] inválida")
        for comarca, lista in (t.get("comarcas", {}) or {}).items():
            checar_lista(lista, f"tribunais.{sigla}.comarcas.{comarca}")

    # feriados nacionais fixos presentes para os anos cobertos (do ano atual até o horizonte)
    anos = {date.today().year}
    if horizonte:
        anos.add(horizonte.year)
    hoje = date.today()
    for ano in sorted(anos):
        for (m, d), nome in FERIADOS_NACIONAIS_FIXOS.items():
            data_f = date(ano, m, d)
            if data_f < hoje or (horizonte and data_f > horizonte):
                continue
            if data_f not in todas_datas:
                erros.append(f"feriado nacional fixo ausente: {data_f.isoformat()} ({nome})")
    return erros


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("uso: validar_calendario.py <arquivo.json>")
        return 2
    erros = validar(Path(argv[1]))
    if erros:
        print("CALENDÁRIO INVÁLIDO:")
        for e in erros:
            print(" -", e)
        return 1
    print("calendário válido")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
