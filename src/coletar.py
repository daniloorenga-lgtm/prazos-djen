"""Coleta no DJEN — API Comunica (CNJ/PJe).

Documentação consultada (17/09/2026):
  - https://comunicaapi.pje.jus.br/swagger/index.html  (Swagger oficial; bloqueado na rede
    do ambiente de construção — parâmetros confirmados por implementações públicas de terceiros
    e pela página https://www.cnj.jus.br/programas-e-acoes/processo-judicial-eletronico-pje/
    comunicacoes-processuais/orientacoes-aos-tribunais/)
  - https://github.com/vbarcelosbt/API_DJEN (nomes de parâmetros e campos da resposta)
Endpoint: GET https://comunicaapi.pje.jus.br/api/v1/comunicacao
Parâmetros: numeroOab, ufOab, dataDisponibilizacaoInicio, dataDisponibilizacaoFim (AAAA-MM-DD),
            siglaTribunal (NÃO usado — §0: todos os tribunais), meio (D/E; não filtrado),
            pagina, itensPorPagina (só 5 ou 100). Máximo de 10.000 resultados por consulta.
Resposta: {"status": ..., "message": ..., "count": N, "items": [...]}
Campos de item observados: id, hash, numero_processo, numeroprocessocommascara, siglaTribunal,
  nomeOrgao, tipoComunicacao, tipoDocumento, nomeClasse, meio, meiocompleto, numeroComunicacao,
  data_disponibilizacao / datadisponibilizacao, link, texto (HTML), destinatarios[{nome, polo}],
  destinatarioadvogados[{advogado:{nome, numero_oab, uf_oab}} ou {nome, numero_oab, uf_oab}],
  ativo, data_cancelamento.
Limite de taxa: cabeçalhos x-ratelimit-*; após 429 esperar ~1 minuto.
Sem autenticação.
Como o Swagger não pôde ser lido diretamente nesta construção, o primeiro `--etapa coletar`
real deve ter sua resposta bruta (logs/djen-AAAA-MM-DD.json) conferida contra estes nomes.
"""
from __future__ import annotations

import html
import json
import logging
import os
import re
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

import requests

from modelos import Publicacao, normalizar_cnj

log = logging.getLogger("coletar")

RE_INSTRUCAO = re.compile(
    r"(ignore\s+(as\s+)?(regras|instru[cç][oõ]es)|desconsidere\s+(as\s+)?instru|"
    r"envie\s+(o\s+)?(e-?mail|relat[oó]rio)\s+para|voc[eê]\s+[eé]\s+um\s+assistente|"
    r"system\s*prompt|prompt\s+injection|assistant:|<\s*/?\s*(system|instruction)\s*>)",
    re.IGNORECASE)


def html_para_texto(t: str) -> str:
    """Remove tags HTML preservando o conteúdo e as quebras de linha. Não resume nem corrige."""
    if not t:
        return ""
    if "<" not in t or ">" not in t:
        return html.unescape(t)
    s = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", t)
    s = re.sub(r"(?i)</\s*(p|div|tr|li|h\d)\s*>", "\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t\xa0]+\n", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip("\n")


def _iso(valor: Any) -> str:
    """Aceita AAAA-MM-DD, AAAA-MM-DDTHH:MM:SS, DD/MM/AAAA. Sem inventar: vazio → 'não informado pela fonte'."""
    if valor is None or str(valor).strip() == "":
        return "não informado pela fonte"
    s = str(valor).strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.match(r"^(\d{2})/(\d{2})/(\d{4})", s)
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    return "não informado pela fonte"


def _str(v: Any) -> str:
    if v is None:
        return "não informado pela fonte"
    s = str(v).strip()
    return s if s else "não informado pela fonte"


def _advogados(item: dict[str, Any]) -> list[dict[str, str]]:
    out = []
    for a in item.get("destinatarioadvogados") or []:
        adv = a.get("advogado") if isinstance(a, dict) and isinstance(a.get("advogado"), dict) else a
        if not isinstance(adv, dict):
            continue
        out.append({"nome": str(adv.get("nome") or ""),
                    "numero_oab": re.sub(r"\D", "", str(adv.get("numero_oab") or "")),
                    "uf_oab": str(adv.get("uf_oab") or "").upper()})
    return out


def _partes(item: dict[str, Any]) -> list[dict[str, str]]:
    out = []
    for d in item.get("destinatarios") or []:
        if isinstance(d, dict):
            out.append({"nome": str(d.get("nome") or ""), "polo": str(d.get("polo") or "")})
    return out


def item_para_publicacao(item: dict[str, Any], advogados_config: list[dict[str, Any]],
                         advogado_consultado: dict[str, Any] | None = None) -> Publicacao:
    numero_original = _str(item.get("numeroprocessocommascara") or item.get("numero_processo"))
    d0 = _iso(item.get("data_disponibilizacao") or item.get("datadisponibilizacao"))
    texto_bruto = item.get("texto") if item.get("texto") is not None else ""
    advs = _advogados(item)
    intimados: list[str] = []
    for cfg in advogados_config:
        for a in advs:
            if a["numero_oab"] == re.sub(r"\D", "", str(cfg["oab"])) and a["uf_oab"] == str(cfg["uf"]).upper():
                if cfg["apelido"] not in intimados:
                    intimados.append(cfg["apelido"])
    if not intimados and advogado_consultado:
        # a API devolveu o item para esta OAB; mesmo sem o campo de advogados, o consultado é intimado
        intimados.append(advogado_consultado["apelido"])
    texto = html_para_texto(str(texto_bruto))
    pub = Publicacao(
        fonte="DJEN",
        id_fonte=_str(item.get("id")),
        tribunal=_str(item.get("siglaTribunal")),
        orgao=_str(item.get("nomeOrgao")),
        numero_processo=normalizar_cnj(numero_original),
        numero_processo_original=numero_original,
        partes=_partes(item),
        advogados=advs,
        intimados=intimados,
        data_disponibilizacao=d0,
        tipo_comunicacao=_str(item.get("tipoComunicacao")),
        tipo_documento=_str(item.get("tipoDocumento")),
        classe=_str(item.get("nomeClasse")),
        meio=_str(item.get("meiocompleto") or item.get("meio")),
        link=_str(item.get("link")),
        texto_integral=texto if texto else "não informado pela fonte",
        texto_bruto=str(texto_bruto),
    )
    if item.get("ativo") is False or item.get("data_cancelamento"):
        pub.observacoes.append(f"fonte marca a comunicação como cancelada/inativa (data_cancelamento={item.get('data_cancelamento')})")
    if RE_INSTRUCAO.search(texto):
        pub.suspeita_instrucao = True
        pub.observacoes.append("texto contém trecho parecido com instrução ao assistente — tratado como dado")
    return pub


class ColetorDJEN:
    def __init__(self, base_url: str, itens_por_pagina: int = 100, max_tentativas: int = 5,
                 timeout: int = 60, sessao: requests.Session | None = None, pausa_base: float = 2.0):
        self.base_url = base_url.rstrip("/")
        self.itens_por_pagina = 100 if itens_por_pagina not in (5, 100) else itens_por_pagina
        self.max_tentativas = max_tentativas
        self.timeout = timeout
        self.sessao = sessao or requests.Session()
        self.sessao.headers.update({"Accept": "application/json", "User-Agent": "prazos-djen/1.0"})
        self.pausa_base = pausa_base

    def _get(self, params: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}/comunicacao"
        ultimo_erro: Exception | None = None
        for tentativa in range(1, self.max_tentativas + 1):
            try:
                r = self.sessao.get(url, params=params, timeout=self.timeout)
                if r.status_code == 429:
                    espera = max(self.pausa_base * (2 ** (tentativa - 1)), 60.0)
                    log.warning("429 do DJEN; aguardando %.0fs (tentativa %d)", espera, tentativa)
                    time.sleep(espera)
                    continue
                if r.status_code >= 500:
                    raise requests.HTTPError(f"HTTP {r.status_code}: {r.text[:200]}")
                r.raise_for_status()
                dados = r.json()
                if not isinstance(dados, dict) or "items" not in dados:
                    raise ValueError(f"resposta sem 'items': {str(dados)[:200]}")
                return dados
            except (requests.RequestException, ValueError) as e:
                ultimo_erro = e
                espera = self.pausa_base * (2 ** (tentativa - 1))
                log.warning("falha DJEN (%s); tentativa %d/%d, aguardando %.0fs", e, tentativa, self.max_tentativas, espera)
                if tentativa < self.max_tentativas:
                    time.sleep(espera)
        raise RuntimeError(f"DJEN indisponível após {self.max_tentativas} tentativas: {ultimo_erro}")

    def consultar_advogado(self, adv: dict[str, Any], inicio: date, fim: date) -> tuple[list[dict[str, Any]], str | None]:
        """Pagina até esgotar. Devolve (itens brutos, erro ou None)."""
        itens: list[dict[str, Any]] = []
        pagina = 1
        erro = None
        while True:
            params = {
                "numeroOab": re.sub(r"\D", "", str(adv["oab"])),
                "ufOab": str(adv["uf"]).upper(),
                "dataDisponibilizacaoInicio": inicio.isoformat(),
                "dataDisponibilizacaoFim": fim.isoformat(),
                "pagina": pagina,
                "itensPorPagina": self.itens_por_pagina,
            }
            try:
                dados = self._get(params)
            except RuntimeError as e:
                erro = f"{adv['nome']} (OAB {adv['oab']}/{adv['uf']}), página {pagina}: {e}"
                break
            lote = dados.get("items") or []
            itens.extend(lote)
            total = dados.get("count")
            if len(lote) < self.itens_por_pagina or (isinstance(total, int) and len(itens) >= total):
                break
            pagina += 1
            if pagina > 100:
                erro = f"{adv['nome']}: mais de 100 páginas — consulta interrompida"
                break
            time.sleep(0.5)
        return itens, erro


class ColetorMock:
    """Fonte simulada para validação sem rede: PRAZOS_DJEN_MOCK=caminho.json com
    {"items": [...]} no formato da API (ou uma lista). Filtra por OAB e janela como a API faria."""

    def __init__(self, caminho: Path):
        with open(caminho, encoding="utf-8") as f:
            dados = json.load(f)
        self.itens = dados.get("items", []) if isinstance(dados, dict) else dados
        log.warning("USANDO FONTE SIMULADA: %s (%d itens)", caminho, len(self.itens))

    def consultar_advogado(self, adv, inicio, fim):
        oab = re.sub(r"\D", "", str(adv["oab"]))
        out = []
        for it in self.itens:
            d = _iso(it.get("data_disponibilizacao") or it.get("datadisponibilizacao"))
            if d != "não informado pela fonte" and not (inicio.isoformat() <= d <= fim.isoformat()):
                continue
            advs = _advogados(it)
            if advs and not any(a["numero_oab"] == oab and a["uf_oab"] == str(adv["uf"]).upper() for a in advs):
                continue
            out.append(it)
        return out, None


def _resumo_erro(erro: str) -> str:
    """Versão curta para o rodapé do e-mail (o texto completo vai em Alertas)."""
    m = re.search(r"(Tunnel connection failed: [^')]+|HTTP \d{3}[^:(]*|timed out|Connection refused|Name or service not known|resposta sem 'items')", erro)
    if m:
        return m.group(1).strip()
    return (erro[:117] + "…") if len(erro) > 120 else erro


def coletar(config: dict[str, Any], inicio: date, fim: date, pasta_logs: Path,
            coletor: ColetorDJEN | None = None) -> tuple[list[Publicacao], list[str], dict[str, str]]:
    """Consulta todos os advogados. Devolve (publicações, erros, status por fonte).
    Nunca lança exceção para fora."""
    fcfg = config["fontes"]["djen"]
    mock = os.environ.get("PRAZOS_DJEN_MOCK")
    if coletor is None and mock:
        coletor = ColetorMock(Path(mock))
    coletor = coletor or ColetorDJEN(fcfg["base_url"], fcfg.get("itens_por_pagina", 100),
                                     fcfg.get("max_tentativas", 5), fcfg.get("timeout_segundos", 60))
    advogados = config["advogados_monitorados"]
    publicacoes: list[Publicacao] = []
    erros: list[str] = []
    status: dict[str, str] = {}
    bruto: dict[str, Any] = {"consultado_em": datetime.now().isoformat(timespec="seconds"),
                             "janela": [inicio.isoformat(), fim.isoformat()], "advogados": {}}
    for adv in advogados:
        chave = f"DJEN {adv['apelido']} (OAB {adv['oab']}/{adv['uf']})"
        try:
            itens, erro = coletor.consultar_advogado(adv, inicio, fim)
        except Exception as e:  # noqa: BLE001 — a rotina nunca pode cair por causa de uma fonte
            itens, erro = [], f"{adv['nome']}: erro inesperado {e!r}"
        bruto["advogados"][adv["apelido"]] = {"erro": erro, "itens": itens}
        if erro:
            erros.append(erro)
            status[chave] = f"falha ({_resumo_erro(erro)})"
        else:
            status[chave] = f"ok ({len(itens)} itens)"
        for item in itens:
            try:
                publicacoes.append(item_para_publicacao(item, advogados, adv))
            except Exception as e:  # noqa: BLE001
                erros.append(f"item {item.get('id')} de {adv['apelido']} não pôde ser normalizado: {e!r}")
    for sec in config["fontes"].get("secundarias") or []:
        status[f"secundária {sec}"] = "não implementada"
        erros.append(f"fonte secundária configurada mas sem coletor: {sec}")
    pasta_logs.mkdir(parents=True, exist_ok=True)
    destino = pasta_logs / f"djen-{fim.isoformat()}.json"
    try:
        with open(destino, "w", encoding="utf-8") as f:
            json.dump(bruto, f, ensure_ascii=False, indent=1)
    except OSError as e:
        erros.append(f"não foi possível gravar {destino}: {e}")
    return publicacoes, erros, status
