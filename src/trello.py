"""Cliente Trello (REST, https://api.trello.com/1). Sem delete, archive ou move — nunca.

Referência: https://developer.atlassian.com/cloud/trello/rest/
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, time as dtime
from typing import Any
from zoneinfo import ZoneInfo

import requests

from dedup import mesmo_ato
from modelos import Cartao

log = logging.getLogger("trello")
API = "https://api.trello.com/1"

ETIQUETAS_NOVAS = {"CONFERIR CONTAGEM": "orange", "DÚVIDA DE CLASSIFICAÇÃO": "purple"}


class ErroTrello(Exception):
    pass


def vencimento_utc(data_iso: str, hora: str = "23:59", fuso: str = "America/Sao_Paulo") -> str:
    h, m = (int(x) for x in hora.split(":"))
    local = datetime.combine(datetime.fromisoformat(data_iso).date(), dtime(h, m), tzinfo=ZoneInfo(fuso))
    return local.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%S.000Z")


class ClienteTrello:
    def __init__(self, key: str | None = None, token: str | None = None, board_id: str | None = None,
                 sessao: requests.Session | None = None, max_tentativas: int = 4, pausa: float = 1.5):
        self.key = key or os.environ.get("TRELLO_KEY", "")
        self.token = token or os.environ.get("TRELLO_TOKEN", "")
        self.board_id = board_id or os.environ.get("TRELLO_BOARD_ID", "")
        if not (self.key and self.token and self.board_id):
            raise ErroTrello("TRELLO_KEY, TRELLO_TOKEN e TRELLO_BOARD_ID são obrigatórios (.env)")
        self.sessao = sessao or requests.Session()
        self.max_tentativas = max_tentativas
        self.pausa = pausa

    # ---------- infra ----------
    def _req(self, metodo: str, caminho: str, **params: Any) -> Any:
        if metodo not in ("GET", "POST", "PUT"):
            raise ErroTrello(f"método {metodo} não permitido neste cliente")
        url = f"{API}{caminho}"
        q = {"key": self.key, "token": self.token}
        ultimo: Exception | None = None
        for tentativa in range(1, self.max_tentativas + 1):
            try:
                if metodo == "GET":
                    r = self.sessao.get(url, params={**q, **params}, timeout=60)
                else:
                    r = self.sessao.request(metodo, url, params=q, json=params, timeout=60)
                if r.status_code == 429:
                    time.sleep(self.pausa * (2 ** tentativa))
                    continue
                if r.status_code >= 500:
                    raise requests.HTTPError(f"HTTP {r.status_code}")
                if r.status_code >= 400:
                    raise ErroTrello(f"HTTP {r.status_code} em {metodo} {caminho}: {r.text[:300]}")
                return r.json() if r.text else {}
            except (requests.RequestException,) as e:
                ultimo = e
                log.warning("Trello %s %s falhou (%s), tentativa %d", metodo, caminho, e, tentativa)
                time.sleep(self.pausa * (2 ** (tentativa - 1)))
        raise ErroTrello(f"Trello indisponível em {metodo} {caminho}: {ultimo}")

    # ---------- descoberta ----------
    def descobrir_ids(self, config: dict[str, Any]) -> dict[str, Any]:
        """Lê o quadro e devolve os IDs para gravar em config.yaml. Cria só as duas etiquetas do §0."""
        tcfg = config["trello"]
        board = self._req("GET", f"/boards/{self.board_id}", fields="name,url")
        listas = self._req("GET", f"/boards/{self.board_id}/lists", fields="name,closed")
        etiquetas = self._req("GET", f"/boards/{self.board_id}/labels", fields="name,color", limit=1000)
        membros = self._req("GET", f"/boards/{self.board_id}/members", fields="fullName,username")

        lista = next((l for l in listas if l["name"].strip().lower() == tcfg["lista_destino"].lower()), None)
        if not lista:
            raise ErroTrello(f"lista '{tcfg['lista_destino']}' não encontrada no quadro")

        ids_etiquetas: dict[str, str | None] = {}
        for nome in ("Prazo Fatal", "Lembrete", *ETIQUETAS_NOVAS):
            e = next((x for x in etiquetas if (x.get("name") or "").strip().lower() == nome.lower()), None)
            if not e and nome in ETIQUETAS_NOVAS:
                e = self._req("POST", "/labels", name=nome, color=ETIQUETAS_NOVAS[nome], idBoard=self.board_id)
                log.info("etiqueta criada: %s", nome)
            ids_etiquetas[nome] = e["id"] if e else None

        resultado_membros = []
        nao_encontrados = []
        for m in tcfg["membros"]:
            alvo = (m.get("nome_trello") or m["nome"]).lower()
            achado = next((x for x in membros if x.get("fullName", "").lower() == alvo), None)
            if not achado:
                primeiro = m["nome"].split()[0].lower()
                cands = [x for x in membros if x.get("fullName", "").lower().split()[:1] == [primeiro]]
                achado = cands[0] if len(cands) == 1 else None
            if achado:
                resultado_membros.append({"nome": m["nome"], "nome_trello": achado["fullName"], "id": achado["id"]})
            else:
                resultado_membros.append({"nome": m["nome"], "nome_trello": m.get("nome_trello"), "id": None})
                nao_encontrados.append(m["nome"])
        return {
            "board": {"id": self.board_id, "nome": board.get("name"), "url": board.get("url")},
            "lista_destino_id": lista["id"],
            "etiquetas": ids_etiquetas,
            "membros": resultado_membros,
            "membros_nao_encontrados": nao_encontrados,
            "membros_do_quadro": [{"id": x["id"], "fullName": x.get("fullName"), "username": x.get("username")} for x in membros],
        }

    # ---------- busca de duplicidade ----------
    def cartoes_do_quadro(self) -> list[dict[str, Any]]:
        """Todos os cartões, inclusive arquivados, paginando por `before`."""
        todos: list[dict[str, Any]] = []
        before = None
        while True:
            params: dict[str, Any] = {"fields": "name,desc,idList,closed,shortUrl,due,idLabels", "limit": 1000}
            if before:
                params["before"] = before
            lote = self._req("GET", f"/boards/{self.board_id}/cards/all", **params)
            if not lote:
                break
            todos.extend(lote)
            if len(lote) < 1000:
                break
            before = lote[-1]["id"]
        return todos

    def buscar_por_processo(self, numero_cnj: str, cartoes: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        if not numero_cnj:
            return []
        cartoes = cartoes if cartoes is not None else self.cartoes_do_quadro()
        return [c for c in cartoes if numero_cnj in (c.get("desc") or "") or numero_cnj in (c.get("name") or "")]

    def existente_para_ato(self, numero_cnj: str, titulo: str, cartoes: list[dict[str, Any]] | None = None,
                           id_lembrete: str | None = None) -> dict[str, Any] | None:
        """Cartão já existente para o mesmo processo + ato. Prefere o cartão de Prazo Fatal: um cartão com a
        etiqueta Lembrete só é devolvido se não houver outro (um par Fatal+Lembrete tem o mesmo título)."""
        candidatos = [c for c in self.buscar_por_processo(numero_cnj, cartoes) if mesmo_ato(c.get("name") or "", titulo)]
        if not candidatos:
            return None
        if id_lembrete:
            sem_lembrete = [c for c in candidatos if id_lembrete not in (c.get("idLabels") or [])]
            if sem_lembrete:
                return sem_lembrete[0]
        return candidatos[0]

    def lembrete_para_ato(self, numero_cnj: str, titulo: str, cartoes: list[dict[str, Any]] | None = None,
                          id_lembrete: str | None = None) -> dict[str, Any] | None:
        """O cartão de Lembrete do mesmo processo + ato (etiqueta Lembrete), se existir."""
        if not id_lembrete:
            return None
        for c in self.buscar_por_processo(numero_cnj, cartoes):
            if mesmo_ato(c.get("name") or "", titulo) and id_lembrete in (c.get("idLabels") or []):
                return c
        return None

    # ---------- criação ----------
    def criar_cartao(self, cartao: Cartao, lista_id: str, ids_membros: list[str], ids_etiquetas: list[str],
                     fuso: str, hora: str) -> Cartao:
        dados = self._req("POST", "/cards", idList=lista_id, name=cartao.titulo, desc=cartao.descricao,
                          due=vencimento_utc(cartao.vencimento, hora, fuso),
                          idMembers=",".join(ids_membros), idLabels=",".join(ids_etiquetas), pos="top")
        cartao.id = dados["id"]
        cartao.url = dados.get("shortUrl") or dados.get("url", "")
        return cartao

    def atualizar_descricao(self, id_cartao: str, desc: str) -> None:
        self._req("PUT", f"/cards/{id_cartao}", desc=desc)

    def comentar(self, id_cartao: str, texto: str) -> None:
        self._req("POST", f"/cards/{id_cartao}/actions/comments", text=texto)

    def atualizar_vencimento(self, id_cartao: str, data_iso: str, fuso: str, hora: str) -> None:
        self._req("PUT", f"/cards/{id_cartao}", due=vencimento_utc(data_iso, hora, fuso))

    def criar_par(self, fatal: Cartao, lembrete: Cartao, lista_id: str, ids_membros: list[str],
                  etiquetas_ids: dict[str, str | None], fuso: str, hora: str) -> tuple[Cartao, Cartao]:
        """§5.4: cria o fatal, depois o lembrete, e grava o link cruzado na descrição de cada um."""
        def ids(nomes: list[str]) -> list[str]:
            out = []
            for n in nomes:
                i = etiquetas_ids.get(n)
                if i:
                    out.append(i)
            return out
        self.criar_cartao(fatal, lista_id, ids_membros, ids(fatal.etiquetas), fuso, hora)
        try:
            self.criar_cartao(lembrete, lista_id, ids_membros, ids(lembrete.etiquetas), fuso, hora)
        except ErroTrello as e:
            lembrete.erro = str(e)
            return fatal, lembrete
        fatal.descricao = fatal.descricao + f"\nCartão vinculado: {lembrete.url} (Lembrete)"
        lembrete.descricao = lembrete.descricao + f"\nCartão vinculado: {fatal.url} (Prazo Fatal)"
        try:
            self.atualizar_descricao(fatal.id, fatal.descricao)
            self.atualizar_descricao(lembrete.id, lembrete.descricao)
        except ErroTrello as e:
            fatal.erro = f"cartões criados, mas o link cruzado não foi gravado: {e}"
        return fatal, lembrete
