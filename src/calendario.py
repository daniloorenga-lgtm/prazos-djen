"""Carregamento do calendário forense (dados/feriados-forenses.json)."""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

RECESSO_INICIO = (12, 20)   # 20/12
RECESSO_FIM = (1, 20)       # 20/01 (retoma em 21/01) — art. 220 CPC


def recessos(anos: list[int]) -> list[tuple[date, date]]:
    """Intervalos de recesso 20/12→20/01 para cada ano de início informado."""
    return [(date(a, *RECESSO_INICIO), date(a + 1, *RECESSO_FIM)) for a in anos]


def _parse_data(s: str) -> date:
    return date.fromisoformat(str(s)[:10])


class Calendario:
    def __init__(self, dados: dict[str, Any] | None, caminho: Path | None = None):
        self.dados = dados or {}
        self.caminho = caminho
        self.extraido_em: datetime | None = None
        if self.dados.get("extraido_em"):
            try:
                self.extraido_em = datetime.fromisoformat(self.dados["extraido_em"])
            except ValueError:
                self.extraido_em = None
        self.tribunais: dict[str, Any] = self.dados.get("tribunais", {}) or {}
        self.nacionais: list[dict[str, Any]] = self.dados.get("feriados_nacionais", []) or []

    @classmethod
    def carregar(cls, caminho: Path) -> "Calendario":
        if not caminho.exists():
            return cls({}, caminho)
        with open(caminho, encoding="utf-8") as f:
            return cls(json.load(f), caminho)

    def tem_tribunal(self, sigla: str | None) -> bool:
        return bool(sigla) and sigla.upper() in {k.upper() for k in self.tribunais}

    def tem_comarca(self, sigla: str | None, comarca: str | None) -> bool:
        if not sigla or not comarca:
            return False
        t = self._tribunal(sigla)
        comarcas = (t or {}).get("comarcas", {}) or {}
        return comarca.strip().lower() in {c.strip().lower() for c in comarcas}

    def _tribunal(self, sigla: str) -> dict[str, Any] | None:
        for k, v in self.tribunais.items():
            if k.upper() == sigla.upper():
                return v
        return None

    def feriados(self, sigla: str | None = None, comarca: str | None = None) -> set[date]:
        """Feriados nacionais + do tribunal + da comarca (quando informados)."""
        out: set[date] = set()
        for f in self.nacionais:
            try:
                out.add(_parse_data(f["data"]))
            except (KeyError, ValueError):
                continue
        t = self._tribunal(sigla) if sigla else None
        if t:
            for f in t.get("feriados", []) or []:
                try:
                    out.add(_parse_data(f["data"]))
                except (KeyError, ValueError):
                    continue
            if comarca:
                for nome, lista in (t.get("comarcas", {}) or {}).items():
                    if nome.strip().lower() == comarca.strip().lower():
                        for f in lista or []:
                            try:
                                out.add(_parse_data(f["data"]))
                            except (KeyError, ValueError):
                                continue
        return out

    def suspensoes(self, sigla: str | None = None, anos: list[int] | None = None) -> list[tuple[date, date]]:
        """Suspensões do tribunal + recesso legal (sempre incluído)."""
        out: list[tuple[date, date]] = []
        t = self._tribunal(sigla) if sigla else None
        if t:
            for s in t.get("suspensoes", []) or []:
                try:
                    out.append((_parse_data(s["inicio"]), _parse_data(s["fim"])))
                except (KeyError, ValueError):
                    continue
        anos = anos or [date.today().year - 1, date.today().year, date.today().year + 1]
        out.extend(recessos(anos))
        return out

    def idade_dias(self, hoje: date | None = None) -> int | None:
        if not self.extraido_em:
            return None
        hoje = hoje or date.today()
        return (hoje - self.extraido_em.date()).days


def ler_status_atualizacao(caminho: Path) -> dict[str, Any] | None:
    """Lê dados/feriados-forenses.status.json (gravado pela rotina semanal quando falha)."""
    if not caminho.exists():
        return None
    try:
        with open(caminho, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"sucesso": False, "motivo": "status.json ilegível"}
