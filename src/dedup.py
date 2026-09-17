"""Hash, estado de processados e detecção de republicação (§2 e prompt §4)."""
from __future__ import annotations

import difflib
import hashlib
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from modelos import Publicacao, normalizar_texto_para_hash

RETENCAO_DIAS = 60
JANELA_REPUBLICACAO_DIAS = 30
LIMIAR_SIMILARIDADE = 0.90


def calcular_hash(numero_cnj: str | None, data_disponibilizacao: str, texto: str) -> str:
    base = f"{numero_cnj or ''}|{data_disponibilizacao or ''}|{normalizar_texto_para_hash(texto)}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def chave_agrupamento(pub: Publicacao) -> str:
    """§2.2: mesma intimação em várias fontes / vários advogados = UMA publicação."""
    return calcular_hash(pub.numero_processo, pub.data_disponibilizacao, pub.texto_integral)


class Processados:
    """estado/processados.json — hashes com data, texto normalizado (para similaridade)."""

    def __init__(self, caminho: Path):
        self.caminho = caminho
        self.itens: dict[str, dict[str, Any]] = {}
        if caminho.exists():
            try:
                with open(caminho, encoding="utf-8") as f:
                    dados = json.load(f)
                self.itens = dados.get("itens", {}) if isinstance(dados, dict) else {}
            except (OSError, ValueError):
                self.itens = {}

    def purgar(self, hoje: date | None = None) -> None:
        hoje = hoje or date.today()
        limite = hoje - timedelta(days=RETENCAO_DIAS)
        self.itens = {h: v for h, v in self.itens.items()
                      if _data(v.get("data")) is None or _data(v.get("data")) >= limite}

    def contem(self, h: str) -> bool:
        return h in self.itens

    def registrar(self, pub: Publicacao, hoje: date | None = None) -> None:
        hoje = hoje or date.today()
        self.itens[pub.hash] = {
            "data": hoje.isoformat(),
            "data_disponibilizacao": pub.data_disponibilizacao,
            "numero_processo": pub.numero_processo,
            "texto_norm": normalizar_texto_para_hash(pub.texto_integral)[:20000],
        }

    def republicacao_de(self, pub: Publicacao, hoje: date | None = None) -> str | None:
        """Devolve o hash da publicação anterior (≤30 dias) do mesmo processo com texto ≥90% similar."""
        if not pub.numero_processo:
            return None
        hoje = hoje or date.today()
        limite = hoje - timedelta(days=JANELA_REPUBLICACAO_DIAS)
        alvo = normalizar_texto_para_hash(pub.texto_integral)
        melhor: tuple[float, str] | None = None
        for h, v in self.itens.items():
            if h == pub.hash or v.get("numero_processo") != pub.numero_processo:
                continue
            d = _data(v.get("data"))
            if d is not None and d < limite:
                continue
            ratio = similaridade(alvo, v.get("texto_norm", ""))
            if ratio >= LIMIAR_SIMILARIDADE and (melhor is None or ratio > melhor[0]):
                melhor = (ratio, h)
        return melhor[1] if melhor else None

    def salvar(self) -> None:
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        with open(self.caminho, "w", encoding="utf-8") as f:
            json.dump({"atualizado_em": datetime.now().isoformat(timespec="seconds"),
                       "itens": self.itens}, f, ensure_ascii=False, indent=1)


def similaridade(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def deduplicar(publicacoes: list[Publicacao]) -> list[Publicacao]:
    """Agrupa por (processo + D0 + hash do texto) e funde os intimados."""
    por_chave: dict[str, Publicacao] = {}
    for p in publicacoes:
        p.hash = chave_agrupamento(p)
        if p.hash in por_chave:
            alvo = por_chave[p.hash]
            for nome in p.intimados:
                if nome not in alvo.intimados:
                    alvo.intimados.append(nome)
            if (p.fonte, p.id_fonte) != (alvo.fonte, alvo.id_fonte):
                obs = f"também recebida via {p.fonte} ({p.id_fonte})"
                if obs not in alvo.observacoes:
                    alvo.observacoes.append(obs)
        else:
            por_chave[p.hash] = p
    return list(por_chave.values())


def _data(s: Any) -> date | None:
    try:
        return date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def ato_do_titulo(titulo: str) -> str:
    """'Parte X Parte | Ato' → 'ato' normalizado para comparação."""
    if "|" not in titulo:
        return ""
    return normalizar_texto_para_hash(titulo.rsplit("|", 1)[1])


def mesmo_ato(titulo_a: str, titulo_b: str) -> bool:
    a, b = ato_do_titulo(titulo_a), ato_do_titulo(titulo_b)
    return bool(a) and a == b
