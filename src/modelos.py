"""Modelos de dados do prazos-djen.

Tudo aqui é determinístico e serializável em JSON: o estado entre etapas
(`estado/*.json`) é composto exclusivamente destas estruturas.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

CATEGORIAS = {
    "PRAZO_EXPRESSO",
    "SENTENCA",
    "ACORDAO",
    "DECISAO_INTERLOCUTORIA",
    "DECISAO_MONOCRATICA_TRIBUNAL",
    "INTIMACAO_CONTRARRAZOES",
    "INTIMACAO_MANIFESTACAO",
    "CUMPRIMENTO_SENTENCA",
    "CITACAO",
    "PAUTA_JULGAMENTO",
    "AUDIENCIA",
    "MERA_CIENCIA",
    "INDETERMINADA",
}

# Categorias que, por definição, não geram cartão em "Prazos" (§3 da especificação).
CATEGORIAS_SEM_CARTAO = {"PAUTA_JULGAMENTO", "AUDIENCIA", "MERA_CIENCIA"}

# Prazos aplicados quando a classificação vier inválida ou INDETERMINADA (§3 / §7).
PRAZOS_INDETERMINADA = [
    {"ato": "Manifestação (regra geral)", "dias": 5, "dias_corridos": False,
     "conferir": True, "motivo": "classificação indeterminada — prazo geral (art. 218 §3º CPC)"},
    {"ato": "Manifestação (prazo de 15 dias)", "dias": 15, "dias_corridos": False,
     "conferir": True, "motivo": "classificação indeterminada — hipótese de prazo de 15 dias"},
]

RE_CNJ = re.compile(r"(\d{7})-?(\d{2})\.?(\d{4})\.?(\d)\.?(\d{2})\.?(\d{4})")


def normalizar_cnj(texto: str | None) -> str | None:
    """Devolve o número no padrão NNNNNNN-DD.AAAA.J.TR.OOOO ou None."""
    if not texto:
        return None
    m = RE_CNJ.search(str(texto))
    if not m:
        return None
    n, dv, ano, j, tr, oooo = m.groups()
    return f"{n}-{dv}.{ano}.{j}.{tr}.{oooo}"


def normalizar_texto_para_hash(texto: str) -> str:
    """Só para hash e similaridade — nunca use o resultado no cartão."""
    t = unicodedata.normalize("NFKD", texto or "")
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"\s+", " ", t).strip().lower()
    return t


@dataclass
class Publicacao:
    fonte: str
    id_fonte: str
    tribunal: str
    orgao: str
    numero_processo: str | None       # CNJ normalizado ou None
    numero_processo_original: str
    partes: list[dict[str, str]]      # [{"nome": ..., "polo": ...}]
    advogados: list[dict[str, str]]   # todos os advogados destinatários da fonte
    intimados: list[str]              # nomes (config) dos advogados do escritório intimados
    data_disponibilizacao: str        # ISO AAAA-MM-DD ou "não informado pela fonte"
    tipo_comunicacao: str
    tipo_documento: str
    classe: str
    meio: str
    link: str
    texto_integral: str               # texto como veio da fonte (tags HTML removidas, conteúdo intacto)
    texto_bruto: str                  # exatamente o campo `texto` da fonte, sem qualquer tratamento
    hash: str = ""
    republicacao: bool = False
    hash_original: str | None = None
    suspeita_instrucao: bool = False
    observacoes: list[str] = field(default_factory=list)

    def d0(self) -> date | None:
        try:
            return date.fromisoformat(self.data_disponibilizacao)
        except (TypeError, ValueError):
            return None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Publicacao":
        campos = {k: d.get(k) for k in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        campos["partes"] = campos.get("partes") or []
        campos["advogados"] = campos.get("advogados") or []
        campos["intimados"] = campos.get("intimados") or []
        campos["observacoes"] = campos.get("observacoes") or []
        return cls(**campos)


@dataclass
class PrazoClassificado:
    """Um prazo devolvido pelo modelo (só número de dias e flags; sem datas)."""
    ato: str
    dias: int
    dias_corridos: bool = False
    conferir: bool = False
    motivo: str = ""


@dataclass
class Classificacao:
    hash: str
    categoria: str
    prazos: list[PrazoClassificado]
    partes: dict[str, str]            # {"autor": ..., "reu": ...}
    duvida: str = ""
    invalida: bool = False            # True quando o JSON do modelo não passou no schema
    erro_schema: str = ""


class ErroSchema(ValueError):
    pass


def validar_classificacao(obj: Any) -> Classificacao:
    """Valida o JSON estrito exigido de o modelo. Levanta ErroSchema se inválido."""
    if not isinstance(obj, dict):
        raise ErroSchema("classificação não é um objeto JSON")
    h = obj.get("hash")
    if not isinstance(h, str) or not h:
        raise ErroSchema("campo 'hash' ausente ou vazio")
    cat = obj.get("categoria")
    if cat not in CATEGORIAS:
        raise ErroSchema(f"categoria inválida: {cat!r}")
    prazos_raw = obj.get("prazos")
    if not isinstance(prazos_raw, list):
        raise ErroSchema("campo 'prazos' deve ser lista")
    prazos: list[PrazoClassificado] = []
    for i, p in enumerate(prazos_raw):
        if not isinstance(p, dict):
            raise ErroSchema(f"prazos[{i}] não é objeto")
        ato = p.get("ato")
        dias = p.get("dias")
        if not isinstance(ato, str) or not ato.strip():
            raise ErroSchema(f"prazos[{i}].ato ausente")
        if isinstance(dias, bool) or not isinstance(dias, int) or dias <= 0 or dias > 365:
            raise ErroSchema(f"prazos[{i}].dias inválido: {dias!r}")
        dc = p.get("dias_corridos", False)
        cf = p.get("conferir", False)
        if not isinstance(dc, bool) or not isinstance(cf, bool):
            raise ErroSchema(f"prazos[{i}] flags devem ser booleanas")
        motivo = p.get("motivo", "")
        if motivo is None:
            motivo = ""
        if not isinstance(motivo, str):
            raise ErroSchema(f"prazos[{i}].motivo deve ser texto")
        prazos.append(PrazoClassificado(ato=ato.strip(), dias=dias, dias_corridos=dc,
                                        conferir=cf, motivo=motivo))
    partes = obj.get("partes") or {}
    if not isinstance(partes, dict):
        raise ErroSchema("campo 'partes' deve ser objeto")
    partes_ok = {k: (str(v) if v is not None else "") for k, v in partes.items()
                 if k in ("autor", "reu")}
    partes_ok.setdefault("autor", "")
    partes_ok.setdefault("reu", "")
    duvida = obj.get("duvida") or ""
    if not isinstance(duvida, str):
        raise ErroSchema("campo 'duvida' deve ser texto")
    if cat not in CATEGORIAS_SEM_CARTAO and cat != "INDETERMINADA" and not prazos:
        raise ErroSchema(f"categoria {cat} exige ao menos um prazo")
    return Classificacao(hash=h, categoria=cat, prazos=prazos, partes=partes_ok, duvida=duvida)


def classificacao_indeterminada(hash_: str, erro: str, partes: dict[str, str] | None = None) -> Classificacao:
    return Classificacao(
        hash=hash_, categoria="INDETERMINADA",
        prazos=[PrazoClassificado(**p) for p in PRAZOS_INDETERMINADA],
        partes=partes or {"autor": "", "reu": ""},
        duvida=f"JSON de classificação inválido: {erro}", invalida=True, erro_schema=erro,
    )


@dataclass
class Cartao:
    tipo: str                         # "fatal" | "lembrete"
    titulo: str
    descricao: str
    vencimento: str                   # ISO AAAA-MM-DD (data local)
    etiquetas: list[str]              # nomes
    membros: list[str]                # nomes (config)
    url: str = ""
    id: str = ""
    erro: str = ""


@dataclass
class PrazoLancado:
    hash_publicacao: str
    numero_processo: str | None
    categoria: str
    ato: str
    dias: int
    dias_corridos: bool
    conferir: bool
    motivo: str
    d0: str
    data_publicacao: str
    dia_1: str
    data_final: str
    data_lembrete: str
    feriados_no_intervalo: list[str]
    atravessou_recesso: bool
    observacoes_contagem: list[str]
    titulo: str
    cliente: str
    intimados: list[str]
    etiquetas: list[str]
    cartao_fatal: dict[str, Any] | None = None
    cartao_lembrete: dict[str, Any] | None = None
    status: str = "PENDENTE"          # LANCADO | NAO_LANCADO | DUPLICADO | DRY_RUN
    detalhe_status: str = ""
