"""Orquestrador da rotina (prompt §8).

Etapas: coletar → (classificação pelo modelo, fora deste script) → contar-e-lancar →
autoverificar → email → fechar.  Estado entre etapas em estado/execucao.json.

Uso:
  python src/rodar.py --etapa coletar [--data AAAA-MM-DD]
  python src/rodar.py --etapa contar-e-lancar [--dry-run|--somente-email]
  python src/rodar.py --etapa autoverificar
  python src/rodar.py --etapa email [--dry-run] [--erro-coleta "motivo"]
  python src/rodar.py --etapa fechar
  python src/rodar.py --etapa descobrir-ids
  python src/rodar.py            # execução completa (para se faltar classificadas.json)
"""
from __future__ import annotations

import argparse
import json
import re
import logging
import shutil
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))
load_dotenv(RAIZ / ".env")

from calendario import Calendario, ler_status_atualizacao  # noqa: E402
from coletar import coletar  # noqa: E402
from contar import contar_prazo, eh_dia_util, lembrete as calc_lembrete  # noqa: E402
from dedup import Processados, deduplicar, mesmo_ato  # noqa: E402
from legalcloud import abrir_conferidor, resolver_conferencia  # noqa: E402
from modelos import (CATEGORIAS_SEM_CARTAO, Cartao, Classificacao, ErroSchema, Publicacao,  # noqa: E402
                     classificacao_indeterminada, validar_classificacao)
import relatorio_email  # noqa: E402

ESTADO = RAIZ / "estado"
LOGS = RAIZ / "logs"
ARQ_EXEC = ESTADO / "execucao.json"
ARQ_PENDENTES = ESTADO / "pendentes.json"
ARQ_CLASSIFICADAS = ESTADO / "classificadas.json"
ARQ_LANCADOS = ESTADO / "lancados.json"
ARQ_ULTIMA = ESTADO / "ultima_execucao.json"
ARQ_PROCESSADOS = ESTADO / "processados.json"

log = logging.getLogger("rodar")


# ---------------------------------------------------------------- utilidades
def carregar_config() -> dict[str, Any]:
    with open(RAIZ / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def ler_json(p: Path, padrao: Any = None) -> Any:
    if not p.exists():
        return padrao
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def gravar_json(p: Path, dados: Any) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=1, default=str)


def configurar_log() -> Path:
    LOGS.mkdir(exist_ok=True)
    arq = LOGS / f"execucao-{datetime.now().strftime('%Y-%m-%d-%H%M')}.log"
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=[logging.FileHandler(arq, encoding="utf-8"), logging.StreamHandler(sys.stdout)])
    return arq


def br(d: date | str | None) -> str:
    if d is None:
        return "?"
    if isinstance(d, str):
        try:
            d = date.fromisoformat(d[:10])
        except ValueError:
            return d
    return d.strftime("%d/%m/%Y")


def dias_uteis_atras(d: date, n: int, feriados: set[date]) -> date:
    x = d
    while n > 0:
        x -= timedelta(days=1)
        if eh_dia_util(x, feriados, []):
            n -= 1
    return x


def janela(config: dict[str, Any], hoje: date, feriados: set[date], forcada: date | None,
           ate: date | None) -> tuple[date, date, str]:
    if forcada:
        return forcada, ate or forcada, "janela forçada por --data"
    ultima = ler_json(ARQ_ULTIMA)
    if ultima and ultima.get("janela_fim"):
        try:
            inicio = date.fromisoformat(ultima["janela_fim"])
            return inicio, hoje, f"desde a última execução bem-sucedida ({ultima.get('executada_em', '')[:16]})"
        except ValueError:
            pass
    n = int(config.get("janela", {}).get("dias_uteis_primeira_execucao", 3))
    return dias_uteis_atras(hoje, n, feriados), hoje, f"janela indeterminada (primeira execução ou falha anterior) — últimos {n} dias úteis"


# ---------------------------------------------------------------- etapa: coletar
def etapa_coletar(args: argparse.Namespace, config: dict[str, Any]) -> int:
    hoje = date.today()
    cal = Calendario.carregar(RAIZ / config["calendario"]["arquivo"])
    status_cal = ler_status_atualizacao(RAIZ / config["calendario"]["status"])
    forcada = date.fromisoformat(args.data) if args.data else None
    ate = date.fromisoformat(args.ate) if args.ate else None
    inicio, fim, motivo = janela(config, hoje, cal.feriados(), forcada, ate)
    if forcada:
        hoje = ate or forcada
    log.info("janela: %s a %s (%s)", inicio, fim, motivo)

    ex: dict[str, Any] = {
        "iniciada_em": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "data_referencia": hoje.isoformat(),
        "janela": {"inicio": inicio.isoformat(), "fim": fim.isoformat(), "motivo": motivo},
        "flags": {"dry_run": bool(args.dry_run), "somente_email": bool(args.somente_email)},
        "alertas": [], "publicacoes": [], "prazos": [], "fontes_status": {}, "erros_coleta": [],
        "calendario": {"extraido_em": cal.extraido_em.strftime("%d/%m/%Y %H:%M") if cal.extraido_em else None,
                       "idade_dias": cal.idade_dias(hoje), "falha_atualizacao": bool(status_cal and not status_cal.get("sucesso", True)),
                       "conferir_todos": False},
        "conferidos_legalcloud": 0, "email": {"enviado": False},
    }
    if "indeterminada" in motivo:
        ex["alertas"].append(f"Janela de busca indeterminada: usados os últimos dias úteis ({br(inicio)} a {br(fim)}).")
    idade = cal.idade_dias(hoje)
    max_idade = int(config["calendario"].get("idade_maxima_dias", 14))
    if idade is None or idade > max_idade or ex["calendario"]["falha_atualizacao"]:
        ex["calendario"]["conferir_todos"] = True
        motivo_cal = ("nunca extraído" if idade is None else f"última extração há {idade} dias" if idade > max_idade
                      else f"última tentativa falhou: {(status_cal or {}).get('motivo', '')}")
        ex["alertas"].append(f"Calendário forense desatualizado ({motivo_cal}): TODOS os prazos do dia marcados CONFERIR CONTAGEM.")

    proc = Processados(ARQ_PROCESSADOS)
    proc.purgar(hoje)

    publicacoes, erros, status = coletar(config, inicio, fim, LOGS)
    ex["fontes_status"] = status
    ex["erros_coleta"] = erros
    for e in erros:
        ex["alertas"].append(f"Fonte com falha: {e}")

    publicacoes = deduplicar(publicacoes)
    ontem = hoje - timedelta(days=1)
    pendentes: list[dict[str, Any]] = []
    for p in publicacoes:
        if proc.contem(p.hash):
            p.observacoes.append("já processada em execução anterior")
            d = p.to_dict()
            d["ja_processada"] = True
            ex["publicacoes"].append(d)
            continue
        h_orig = proc.republicacao_de(p, hoje)
        if h_orig:
            p.republicacao = True
            p.hash_original = h_orig
        d0 = p.d0()
        if d0 is None:
            p.observacoes.append("data de disponibilização não informada pela fonte — CONFERIR")
            ex["alertas"].append(f"Publicação {p.numero_processo or p.numero_processo_original} sem data de disponibilização na fonte.")
        elif d0 < ontem:
            ex["alertas"].append(f"D0 ANTIGA: {p.numero_processo or p.numero_processo_original} disponibilizada em {br(d0)} — o prazo pode estar correndo há dias.")
        if not p.numero_processo:
            ex["alertas"].append(f"Publicação {p.id_fonte} sem número CNJ reconhecível ('{p.numero_processo_original}'): busca de duplicidade impossível.")
        if p.suspeita_instrucao:
            ex["alertas"].append(f"Publicação {p.numero_processo or p.id_fonte}: texto contém trecho parecido com instrução ao assistente — tratado como dado.")
        d = p.to_dict()
        d["ja_processada"] = False
        ex["publicacoes"].append(d)
        pendentes.append({
            "hash": p.hash, "numero_processo": p.numero_processo, "numero_processo_original": p.numero_processo_original,
            "tribunal": p.tribunal, "orgao": p.orgao, "data_disponibilizacao": p.data_disponibilizacao,
            "tipo_comunicacao": p.tipo_comunicacao, "tipo_documento": p.tipo_documento, "classe": p.classe,
            "partes_fonte": p.partes, "intimados": p.intimados, "republicacao": p.republicacao,
            "observacoes": p.observacoes, "texto_integral": p.texto_integral,
        })
    gravar_json(ARQ_PENDENTES, {"gerado_em": datetime.now().isoformat(timespec="seconds"),
                                "instrucoes": "Para cada item, produza a classificação JSON conforme CLAUDE.md e grave a lista em estado/classificadas.json",
                                "publicacoes": pendentes})
    if ARQ_CLASSIFICADAS.exists():
        ARQ_CLASSIFICADAS.unlink()   # classificações antigas nunca valem para uma coleta nova
    if ARQ_LANCADOS.exists():
        ARQ_LANCADOS.unlink()
    gravar_json(ARQ_EXEC, ex)
    print(f"COLETA: {len(publicacoes)} publicações na janela {br(inicio)}–{br(fim)}; "
          f"{len(pendentes)} a classificar ({len(publicacoes) - len(pendentes)} já processadas); {len(erros)} erro(s) de fonte.")
    for e in erros:
        print(f"ERRO fonte: {e}")
    print(f"→ {ARQ_PENDENTES}")
    return 0


# ---------------------------------------------------------------- etapa: contar-e-lancar
def montar_titulo(cls: Classificacao, pub: dict[str, Any], ato: str) -> tuple[str, str]:
    autor = (cls.partes.get("autor") or "").strip()
    reu = (cls.partes.get("reu") or "").strip()
    if not (autor and reu):
        nomes = [x.get("nome", "") for x in pub.get("partes", []) if x.get("nome")]
        autor = autor or (nomes[0] if nomes else "Parte não informada")
        reu = reu or (nomes[1] if len(nomes) > 1 else "Parte não informada")
    partes = f"{autor} X {reu}"
    return f"{partes} | {ato}", partes


def montar_descricao(pub: dict[str, Any], res, lemb: date, cls: Classificacao, prazo, obs_extra: list[str],
                     data_final: date | None = None, linha_legalcloud: str | None = None) -> str:
    data_final = data_final or res.data_final
    fer = ", ".join(d.strftime("%d/%m") for d in res.feriados_no_intervalo) or "nenhum"
    linhas = [
        f"{pub['fonte']} - {pub['tribunal']}",
        "",
        f"Disponibilização: {br(pub['data_disponibilizacao'])}",
        "",
        pub["texto_integral"],
        "",
        "---",
        f"Contagem: {res.linha_contagem()}",
        f"Feriados/suspensões no intervalo: {fer}" + (" · atravessa o recesso 20/12–20/01" if res.atravessou_recesso else ""),
        f"Cartões desta publicação: Prazo Fatal {data_final.strftime('%d/%m')} | Lembrete {lemb.strftime('%d/%m')}",
        f"Categoria: {cls.categoria} · Intimado: {', '.join(pub.get('intimados') or ['não identificado'])}",
        f"Ato: {prazo.ato} · {prazo.dias} dias {'corridos' if prazo.dias_corridos else 'úteis'}" + (f" · {prazo.motivo}" if prazo.motivo else ""),
    ]
    if cls.duvida:
        linhas.append(f"Dúvida: {cls.duvida}")
    for o in obs_extra:
        linhas.append(f"Obs.: {o}")
    if linha_legalcloud:
        linhas.append(linha_legalcloud)
    elif "CONFERIR CONTAGEM" in obs_extra or any("CONFERIR" in o for o in obs_extra):
        linhas.append("Legalcloud: pendente de conferência")
    return "\n".join(linhas)


def etapa_contar_e_lancar(args: argparse.Namespace, config: dict[str, Any]) -> int:
    ex = ler_json(ARQ_EXEC)
    if not ex:
        print("ERRO: estado/execucao.json não existe — rode --etapa coletar antes.")
        return 2
    ex["flags"]["dry_run"] = ex["flags"].get("dry_run") or bool(args.dry_run)
    ex["flags"]["somente_email"] = ex["flags"].get("somente_email") or bool(args.somente_email)
    sem_trello = ex["flags"]["dry_run"] or ex["flags"]["somente_email"]
    hoje = date.fromisoformat(ex["data_referencia"])
    cal = Calendario.carregar(RAIZ / config["calendario"]["arquivo"])
    pendentes = (ler_json(ARQ_PENDENTES) or {}).get("publicacoes", [])
    pubs_por_hash = {p["hash"]: p for p in ex["publicacoes"]}
    if not pendentes:
        print("Nada a classificar/lançar.")
        ex["prazos"] = []
        gravar_json(ARQ_EXEC, ex)
        gravar_json(ARQ_LANCADOS, [])
        return 0

    brutas = ler_json(ARQ_CLASSIFICADAS)
    if brutas is None:
        print(f"ERRO: {ARQ_CLASSIFICADAS} não existe — o modelo precisa classificar estado/pendentes.json antes.")
        return 3
    if isinstance(brutas, dict) and "classificacoes" in brutas:
        brutas = brutas["classificacoes"]
    if not isinstance(brutas, list):
        brutas = [brutas]
    classes: dict[str, Classificacao] = {}
    for obj in brutas:
        try:
            c = validar_classificacao(obj)
        except ErroSchema as e:
            h = obj.get("hash") if isinstance(obj, dict) else None
            if h and h in pubs_por_hash:
                classes[h] = classificacao_indeterminada(h, str(e))
                ex["alertas"].append(f"Classificação inválida para {pubs_por_hash[h].get('numero_processo')} ({e}) — tratada como INDETERMINADA (5 e 15).")
            else:
                ex["alertas"].append(f"Classificação sem hash reconhecível ignorada: {e}")
            continue
        classes[c.hash] = c
    for p in pendentes:
        if p["hash"] not in classes:
            classes[p["hash"]] = classificacao_indeterminada(p["hash"], "publicação não classificada pelo modelo")
            ex["alertas"].append(f"Publicação {p.get('numero_processo') or p['numero_processo_original']} ficou sem classificação — tratada como INDETERMINADA (5 e 15).")

    cliente_trello = None
    cartoes_quadro: list[dict[str, Any]] = []
    tcfg = config["trello"]
    if not sem_trello:
        try:
            from trello import ClienteTrello, ErroTrello
            cliente_trello = ClienteTrello(board_id=tcfg.get("board_id"))
            cartoes_quadro = cliente_trello.cartoes_do_quadro()
            log.info("%d cartões no quadro (inclusive arquivados)", len(cartoes_quadro))
        except Exception as e:  # noqa: BLE001
            ex["alertas"].append(f"Trello indisponível: {e!r} — nenhum cartão será criado nesta execução.")
            cliente_trello = None
    ids_membros = [m["id"] for m in tcfg["membros"] if m.get("id")]
    membros_sem_id = [m["nome"] for m in tcfg["membros"] if not m.get("id")]
    if membros_sem_id:
        ex["alertas"].append(f"Membros sem ID no config.yaml (não serão atribuídos): {', '.join(membros_sem_id)}. Rode --etapa descobrir-ids e confirme.")
    etiquetas_ids = tcfg.get("etiquetas", {}) or {}
    for nome in ("Prazo Fatal", "Lembrete", "CONFERIR CONTAGEM", "DÚVIDA DE CLASSIFICAÇÃO"):
        if not etiquetas_ids.get(nome) and not sem_trello:
            ex["alertas"].append(f"Etiqueta '{nome}' sem ID no config.yaml — cartões sairão sem ela. Rode --etapa descobrir-ids.")

    lccfg = config.get("legalcloud") or {}
    estado_lc: dict[str, Any] = {"conferidor": None, "tentado": False, "indisponivel": None}
    usar_lc = not args.sem_legalcloud and (not ex["flags"]["dry_run"] or lccfg.get("conferir_no_dry_run", True))
    ex["conferidos_legalcloud"] = 0
    ex["legalcloud"] = {"usado": False, "indisponivel": None, "divergencias": 0}

    def obter_conferidor():
        if estado_lc["tentado"]:
            return estado_lc["conferidor"]
        estado_lc["tentado"] = True
        if not usar_lc:
            estado_lc["indisponivel"] = "desligado por --sem-legalcloud" if args.sem_legalcloud else "desligado em dry-run (legalcloud.conferir_no_dry_run=false)"
        else:
            try:
                c = abrir_conferidor(lccfg, LOGS)
                c.__enter__()
                estado_lc["conferidor"] = c
                ex["legalcloud"]["usado"] = True
            except Exception as e:  # noqa: BLE001
                estado_lc["indisponivel"] = str(e)
        if estado_lc["indisponivel"]:
            ex["legalcloud"]["indisponivel"] = estado_lc["indisponivel"]
            ex["alertas"].append(f"Legalcloud indisponível ({estado_lc['indisponivel']}): prazos CONFERIR CONTAGEM ficaram sem conferência no site.")
        return estado_lc["conferidor"]

    prazos_out: list[dict[str, Any]] = []
    criados_nesta_execucao: list[str] = []
    ontem = hoje - timedelta(days=1)
    for p in pendentes:
        cls = classes[p["hash"]]
        pub = pubs_por_hash[p["hash"]]
        pub["categoria"] = cls.categoria
        pub["duvida"] = cls.duvida
        pub["resumo"] = cls.duvida if cls.categoria in CATEGORIAS_SEM_CARTAO else ""
        pub["resumo"] = "; ".join(f"{x.ato} ({x.dias}d)" for x in cls.prazos) or pub["resumo"] or cls.categoria.replace("_", " ").lower()
        if cls.invalida or cls.categoria == "INDETERMINADA":
            ex["alertas"].append(f"INDETERMINADA: {pub.get('numero_processo') or pub['numero_processo_original']} — {cls.duvida or 'sem motivo'} (lançados 5 e 15).")
        if cls.categoria in CATEGORIAS_SEM_CARTAO:
            if cls.prazos:
                ex["alertas"].append(f"{pub.get('numero_processo')}: categoria {cls.categoria} veio com prazos — lançados por segurança (§3, regra de desempate).")
            else:
                continue
        d0 = None
        try:
            d0 = date.fromisoformat(pub["data_disponibilizacao"])
        except ValueError:
            pass
        if d0 is None:
            d0 = hoje
            ex["alertas"].append(f"{pub.get('numero_processo')}: sem D0 na fonte — contagem feita a partir de HOJE ({br(hoje)}) por segurança; CONFERIR.")
        comarca = next((c for c in (config.get("comarcas", {}) or {}).get(pub["tribunal"], []) if c.lower() in (pub.get("orgao") or "").lower()), None)
        feriados = cal.feriados(pub["tribunal"], comarca)
        suspensoes = cal.suspensoes(pub["tribunal"], [d0.year - 1, d0.year, d0.year + 1])
        for prazo in cls.prazos:
            res = contar_prazo(d0, prazo.dias, feriados, suspensoes, prazo.dias_corridos)
            lemb = calc_lembrete(res.data_final, prazo.dias, feriados, suspensoes, dia_1=res.dia_1)
            obs: list[str] = list(res.observacoes)
            conferir = prazo.conferir or prazo.dias_corridos or bool(res.feriados_no_intervalo) or res.atravessou_recesso \
                or ex["calendario"].get("conferir_todos") or not cal.tem_tribunal(pub["tribunal"]) or pub["data_disponibilizacao"] != d0.isoformat()
            if not cal.tem_tribunal(pub["tribunal"]):
                obs.append(f"calendário forense não tem o tribunal {pub['tribunal']} — CONFERIR CONTAGEM")
            if comarca is None and pub["tribunal"] in (config.get("comarcas") or {}):
                obs.append("comarca do processo não identificada no calendário — feriados municipais não considerados")
            if d0 < ontem:
                obs.append(f"D0 anterior a ontem ({br(d0)}): o prazo pode já estar correndo há dias")
            data_final = res.data_final
            linha_lc = None
            conferencia: dict[str, Any] | None = None
            # Regra do escritório: o Legalcloud confere TODOS os prazos, todo dia (legalcloud.conferir_todos, padrão true).
            # A etiqueta CONFERIR CONTAGEM continua reservada aos casos que merecem olhar humano.
            conferir_no_site = conferir or bool(lccfg.get("conferir_todos", True))
            if conferir_no_site:
                conferidor = obter_conferidor()
                if conferidor is not None:
                    # regra do DJEN: tudo se conta da DISPONIBILIZAÇÃO (D0); só informe a publicação se o config mandar
                    data_inf = res.data_publicacao if lccfg.get("data_informada") == "publicacao" else d0
                    contexto = f"{pub.get('orgao') or ''} {pub.get('classe') or ''}".lower()
                    regime = ("CPP" if prazo.dias_corridos and re.search(r"penal|criminal|execu[cç][aã]o penal", contexto)
                              else "Juizado Especial" if prazo.dias_corridos else "Novo CPC")
                    instancia = "2ª Instância" if re.search(r"c[aâ]mara|turma|desembargador|relator|se[cç][aã]o|[oó]rg[aã]o especial|plen[aá]rio", contexto) \
                        or pub["tribunal"] in ("STJ", "STF") else "1ª Instância"
                    rc = conferidor.conferir(pub["tribunal"], pub.get("classe") or "", data_inf, prazo.dias, prazo.dias_corridos,
                                             res.data_final, regime=regime, instancia=instancia, fonte=pub.get("fonte") or "DJEN")
                    data_final, linha_lc, divergiu = resolver_conferencia(res.data_final, rc)
                    conferencia = {"data_site": rc.data_site.isoformat() if rc.data_site else None, "confere": rc.confere,
                                   "observacao": rc.observacao, "captura": rc.captura, "divergiu": divergiu}
                    if rc.data_site is not None:
                        ex["conferidos_legalcloud"] += 1
                    else:
                        ex["alertas"].append(f"Legalcloud não conferiu {pub.get('numero_processo')} · {prazo.ato}: {rc.observacao}")
                        conferir = True   # sem confirmação do site, o prazo vai para conferência humana
                    if divergiu:
                        ex["legalcloud"]["divergencias"] += 1
                        lemb = calc_lembrete(data_final, prazo.dias, feriados, suspensoes, dia_1=res.dia_1)
                        obs.append(linha_lc)
                        ex["alertas"].append(f"DIVERGÊNCIA Legalcloud: {pub.get('numero_processo')} · {prazo.ato} — site {br(rc.data_site)} × local {br(res.data_final)}; lançado o mais curto ({br(data_final)}).")
                else:
                    conferir = True       # site indisponível: conferência humana
            etiquetas = []
            if conferir:
                etiquetas.append("CONFERIR CONTAGEM")
            if cls.categoria == "INDETERMINADA" or cls.invalida:
                etiquetas.append("DÚVIDA DE CLASSIFICAÇÃO")
            elif cls.duvida:
                etiquetas.append("DÚVIDA DE CLASSIFICAÇÃO")
            titulo, partes = montar_titulo(cls, pub, prazo.ato)
            desc = montar_descricao(pub, res, lemb, cls, prazo, obs + (["CONFERIR CONTAGEM"] if conferir else []),
                                    data_final=data_final, linha_legalcloud=linha_lc)
            item: dict[str, Any] = {
                "hash_publicacao": pub["hash"], "numero_processo": pub.get("numero_processo"), "categoria": cls.categoria,
                "ato": prazo.ato, "dias": prazo.dias, "dias_corridos": prazo.dias_corridos, "conferir": conferir,
                "motivo": prazo.motivo, "d0": d0.isoformat(), "data_publicacao": res.data_publicacao.isoformat(),
                "dia_1": res.dia_1.isoformat(), "data_final": data_final.isoformat(), "data_lembrete": lemb.isoformat(),
                "data_final_local": res.data_final.isoformat(), "legalcloud": conferencia,
                "feriados_no_intervalo": [d.isoformat() for d in res.feriados_no_intervalo],
                "atravessou_recesso": res.atravessou_recesso, "observacoes_contagem": obs, "titulo": titulo,
                "cliente": partes, "intimados": pub.get("intimados", []), "etiquetas": etiquetas,
                "cartao_fatal": None, "cartao_lembrete": None, "status": "PENDENTE", "detalhe_status": "",
                "republicacao": bool(pub.get("republicacao")),
            }
            fatal = Cartao("fatal", titulo, desc, data_final.isoformat(), ["Prazo Fatal", *etiquetas], [m["nome"] for m in tcfg["membros"]])
            lembr = Cartao("lembrete", titulo, desc, lemb.isoformat(), ["Lembrete", *etiquetas], [m["nome"] for m in tcfg["membros"]])

            # §5.1 duplicidade: no quadro (inclusive arquivados) e nesta execução
            existente = None
            if pub.get("numero_processo"):
                if cliente_trello:
                    existente = cliente_trello.existente_para_ato(pub["numero_processo"], titulo, cartoes_quadro)
                chave_local = f"{pub['numero_processo']}|{titulo.rsplit('|', 1)[-1].strip().lower()}"
                if chave_local in criados_nesta_execucao:
                    existente = existente or {"name": titulo, "shortUrl": "(criado nesta execução)", "id": None, "due": None}
                criados_nesta_execucao.append(chave_local)
            if existente:
                item["status"] = "DUPLICADO"
                due_existente = (existente.get("due") or "")[:10]
                mudou = bool(due_existente) and due_existente != data_final.isoformat() and not (existente.get("due") and _mesma_data_local(existente["due"], data_final))
                item["detalhe_status"] = (f"já existe cartão para o mesmo ato: {existente.get('shortUrl')}"
                                          + (f" — prazo DIFERENTE (cartão: {due_existente}; calculado: {data_final.isoformat()})" if mudou else " — prazo mantido"))
                if cliente_trello and existente.get("id"):
                    try:
                        if mudou:
                            cliente_trello.atualizar_vencimento(existente["id"], data_final.isoformat(), tcfg["fuso"], tcfg["hora_vencimento"])
                            cliente_trello.comentar(existente["id"], f"Republicação/nova intimação em {br(d0)} — prazo ATUALIZADO para {br(data_final)}. Contagem: {res.linha_contagem()}")
                            item["status"] = "ATUALIZADO"
                            item["cartao_fatal"] = {"url": existente.get("shortUrl"), "id": existente["id"]}
                        else:
                            cliente_trello.comentar(existente["id"], f"Republicação/nova intimação em {br(d0)} — prazo mantido ({br(data_final)}).")
                    except Exception as e:  # noqa: BLE001
                        item["detalhe_status"] += f" (falha ao comentar: {e})"
                ex["alertas"].append(f"Duplicidade: {titulo} — {item['detalhe_status']}")
                prazos_out.append(item)
                continue

            if sem_trello:
                item["status"] = "DRY_RUN"
                item["detalhe_status"] = "modo de validação — cartão não criado"
                item["cartao_fatal"] = {"titulo": fatal.titulo, "vencimento": fatal.vencimento, "etiquetas": fatal.etiquetas, "descricao": fatal.descricao}
                item["cartao_lembrete"] = {"titulo": lembr.titulo, "vencimento": lembr.vencimento, "etiquetas": lembr.etiquetas}
            elif cliente_trello is None:
                item["status"] = "NAO_LANCADO"
                item["detalhe_status"] = "Trello indisponível"
            else:
                try:
                    fatal, lembr = cliente_trello.criar_par(fatal, lembr, tcfg["lista_destino_id"], ids_membros, etiquetas_ids,
                                                            tcfg["fuso"], tcfg["hora_vencimento"])
                    item["cartao_fatal"] = {"id": fatal.id, "url": fatal.url, "erro": fatal.erro}
                    item["cartao_lembrete"] = {"id": lembr.id, "url": lembr.url, "erro": lembr.erro}
                    if lembr.erro and not lembr.id:
                        item["status"] = "NAO_LANCADO"
                        item["detalhe_status"] = f"cartão fatal criado ({fatal.url}) mas o LEMBRETE falhou: {lembr.erro}"
                    else:
                        item["status"] = "LANCADO"
                        item["detalhe_status"] = fatal.erro or ""
                except Exception as e:  # noqa: BLE001
                    item["status"] = "NAO_LANCADO"
                    item["detalhe_status"] = f"{e!r}"
            prazos_out.append(item)
            print(f"{item['status']}: {titulo} · fatal {br(data_final)} · lembrete {br(lemb)} · {', '.join(etiquetas) or '-'}"
                  + (f" · {linha_lc}" if linha_lc else "")
                  + (f" · {item['detalhe_status']}" if item["detalhe_status"] else ""))
            if item["status"] == "NAO_LANCADO":
                print(f"NAO_LANCADO: {titulo} — {item['detalhe_status']}")

    if estado_lc["conferidor"] is not None:
        try:
            estado_lc["conferidor"].__exit__(None, None, None)
        except Exception as e:  # noqa: BLE001
            log.warning("erro ao fechar o Legalcloud: %s", e)
    print(f"LEGALCLOUD: {ex['conferidos_legalcloud']} prazo(s) conferido(s) · {ex['legalcloud']['divergencias']} divergência(s)"
          + (f" · indisponível: {ex['legalcloud']['indisponivel']}" if ex['legalcloud']['indisponivel'] else ""))
    ex["prazos"] = prazos_out
    gravar_json(ARQ_LANCADOS, prazos_out)
    gravar_json(ARQ_EXEC, ex)
    n_l = sum(1 for x in prazos_out if x["status"] in ("LANCADO", "ATUALIZADO"))
    n_n = sum(1 for x in prazos_out if x["status"] == "NAO_LANCADO")
    n_d = sum(1 for x in prazos_out if x["status"] == "DRY_RUN")
    print(f"LANÇAMENTO: {len(prazos_out)} prazos · {n_l} lançados · {n_d} simulados · {n_n} NAO_LANCADO · "
          f"{sum(1 for x in prazos_out if x['status'] == 'DUPLICADO')} duplicados")
    return 0


def _mesma_data_local(due_utc: str, data_local: date, fuso: str = "America/Sao_Paulo") -> bool:
    from zoneinfo import ZoneInfo
    try:
        dt = datetime.fromisoformat(due_utc.replace("Z", "+00:00")).astimezone(ZoneInfo(fuso))
        return dt.date() == data_local
    except ValueError:
        return False


# ---------------------------------------------------------------- etapa: autoverificar
def etapa_autoverificar(args: argparse.Namespace, config: dict[str, Any]) -> int:
    ex = ler_json(ARQ_EXEC)
    if not ex:
        print("ERRO: sem estado/execucao.json")
        return 2
    pubs = [p for p in ex["publicacoes"] if not p.get("ja_processada")]
    prazos = ex.get("prazos", [])
    ok_total = True
    hoje = date.fromisoformat(ex["data_referencia"])
    ontem = hoje - timedelta(days=1)

    # 1
    sem_cat = [p for p in pubs if not p.get("categoria")]
    com_prazo_sem_cartao = [x for x in prazos if x["status"] not in ("LANCADO", "ATUALIZADO", "DRY_RUN", "DUPLICADO")
                            and not (x["status"] == "NAO_LANCADO" and x["detalhe_status"])]
    r1 = not sem_cat and not com_prazo_sem_cartao
    print(f"1. Toda publicação tem categoria e todo prazo tem cartão ou alerta: {'OK' if r1 else 'REPROVADO'}")
    for p in sem_cat:
        print(f"   - sem categoria: {p.get('numero_processo')}")
    for x in com_prazo_sem_cartao:
        print(f"   - prazo sem cartão nem alerta: {x['titulo']} ({x['status']})")
    # 2
    problemas2 = []
    membros_ids = [m for m in config["trello"]["membros"] if m.get("id")]
    for x in prazos:
        if x["status"] in ("LANCADO",):
            if not (x.get("cartao_fatal") or {}).get("url") or not (x.get("cartao_lembrete") or {}).get("url"):
                problemas2.append(f"{x['titulo']}: faltou cartão fatal ou lembrete")
        if x["status"] in ("LANCADO", "DRY_RUN"):
            if len(membros_ids) != 4:
                problemas2.append(f"{x['titulo']}: cartões com {len(membros_ids)} membros com ID em vez de 4")
            if not x.get("data_final") or not x.get("data_lembrete"):
                problemas2.append(f"{x['titulo']}: sem vencimento")
    r2 = not problemas2
    print(f"2. Cada prazo com dois cartões (Fatal + Lembrete), quatro membros, etiqueta, vencimento e recorte integral: {'OK' if r2 else 'REPROVADO'}")
    for s in sorted(set(problemas2)):
        print(f"   - {s}")
    # 3
    chaves = [(x["numero_processo"], x["titulo"].rsplit("|", 1)[-1].strip().lower()) for x in prazos if x["status"] in ("LANCADO", "DRY_RUN")]
    dup = {c for c in chaves if chaves.count(c) > 1}
    r3 = not dup
    print(f"3. Nenhum cartão duplicado criado: {'OK' if r3 else 'REPROVADO'}")
    for c in dup:
        print(f"   - duplicado nesta execução: {c}")
    # 4
    r4 = all(x.get("d0") and x.get("data_publicacao") and x.get("dia_1") and x.get("data_final") for x in prazos)
    print(f"4. Todas as contagens mostram D0 → publicação → dia 1 → final: {'OK' if r4 else 'REPROVADO'}")
    # 5
    antigas = [p for p in pubs if _d(p.get("data_disponibilizacao")) and _d(p["data_disponibilizacao"]) < ontem]
    faltando = [p for p in antigas if not any("D0 ANTIGA" in a and (p.get("numero_processo") or "") in a for a in ex["alertas"])]
    r5 = not faltando
    print(f"5. Publicação com D0 anterior a ontem sem alerta: {'nenhuma — OK' if r5 else 'REPROVADO'}")
    for p in faltando:
        print(f"   - {p.get('numero_processo')} D0 {p['data_disponibilizacao']}")
    ok_total = r1 and r2 and r3 and r4 and r5
    ex["autoverificacao"] = {"ok": ok_total, "itens": [r1, r2, r3, r4, r5], "em": datetime.now().isoformat(timespec="seconds")}
    if not ok_total:
        ex["alertas"].append("Autoverificação (§8) reprovou em ao menos um item — ver log da execução.")
    gravar_json(ARQ_EXEC, ex)
    print(f"AUTOVERIFICAÇÃO: {'APROVADA' if ok_total else 'REPROVADA'}")
    return 0 if ok_total else 1


def _d(s: Any) -> date | None:
    try:
        return date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- etapa: email
def etapa_email(args: argparse.Namespace, config: dict[str, Any]) -> int:
    ex = ler_json(ARQ_EXEC)
    if args.erro_coleta is not None:
        hoje = date.today()
        ex = ex if ex and ex.get("data_referencia") == hoje.isoformat() else {
            "iniciada_em": datetime.now().strftime("%d/%m/%Y %H:%M"), "data_referencia": hoje.isoformat(),
            "janela": {"inicio": hoje.isoformat(), "fim": hoje.isoformat(), "motivo": "coleta falhou"},
            "flags": {"dry_run": bool(args.dry_run), "somente_email": False}, "alertas": [], "publicacoes": [],
            "prazos": [], "fontes_status": {"DJEN": "falha"}, "erros_coleta": [], "calendario": {}, "email": {"enviado": False}}
        ex["erro_coleta"] = args.erro_coleta or "falha na coleta (duas tentativas)"
        ex["alertas"] = [a for a in ex["alertas"] if not a.startswith("COLETA FALHOU")]
        ex["alertas"].append(f"COLETA FALHOU duas vezes: {ex['erro_coleta']}. Publicações do dia NÃO foram processadas.")
    if not ex:
        print("ERRO: sem estado/execucao.json")
        return 2
    ex["flags"]["dry_run"] = bool(args.dry_run)   # decidido por ESTA invocação, não pelo estado de etapas anteriores
    ecfg = config["email"]
    destinatarios = list(ecfg["destinatarios"])
    if args.para:
        pedidos = [d.strip().lower() for d in args.para.split(",") if d.strip()]
        fora = [d for d in pedidos if d not in [x.lower() for x in destinatarios]]
        if fora:
            print(f"ERRO: --para só aceita destinatários do config.yaml; não permitidos: {fora}")
            return 2
        destinatarios = [d for d in destinatarios if d.lower() in pedidos]
        ex["alertas"] = [a for a in ex["alertas"] if not a.startswith("E-MAIL DE TESTE")]
        ex["alertas"].insert(0, f"E-MAIL DE TESTE enviado só para {', '.join(destinatarios)} (--para).")
    assunto, html, texto = relatorio_email.montar_email(ex)
    anexo = relatorio_email.anexo_recortes(ex)
    nome_anexo = f"recortes-{ex['data_referencia']}.txt"
    saida = LOGS / f"email-{ex['data_referencia']}.html"
    saida.write_text(html, encoding="utf-8")
    (LOGS / f"email-{ex['data_referencia']}.txt").write_text(texto, encoding="utf-8")
    (LOGS / nome_anexo).write_text(anexo, encoding="utf-8")
    print(f"ASSUNTO: {assunto}")
    print(texto)
    print(f"\n(cópias em {saida} e {LOGS / nome_anexo})")
    if ex["flags"]["dry_run"]:
        print("DRY-RUN: e-mail NÃO enviado.")
        ex["email"] = {"enviado": False, "assunto": assunto, "dry_run": True}
        gravar_json(ARQ_EXEC, ex)
        return 0
    try:
        resp = relatorio_email.enviar(ecfg["remetente"], destinatarios, assunto, html, texto, nome_anexo, anexo)
        ex["email"] = {"enviado": True, "assunto": assunto, "id": resp.get("id"), "em": datetime.now().isoformat(timespec="seconds"),
                       "n_prazos": sum(1 for x in ex.get("prazos", []) if x["status"] in ("LANCADO", "ATUALIZADO", "DRY_RUN"))}
        print(f"EMAIL ENVIADO: id={resp.get('id')} · assunto='{assunto}' · prazos={ex['email']['n_prazos']}")
        rc = 0
    except Exception as e:  # noqa: BLE001
        ex["email"] = {"enviado": False, "assunto": assunto, "erro": repr(e)}
        print(f"ERRO ao enviar e-mail: {e!r}")
        rc = 1
    gravar_json(ARQ_EXEC, ex)
    return rc


# ---------------------------------------------------------------- etapa: fechar
def etapa_fechar(args: argparse.Namespace, config: dict[str, Any]) -> int:
    ex = ler_json(ARQ_EXEC)
    if not ex:
        print("ERRO: sem estado/execucao.json")
        return 2
    if not ex.get("email", {}).get("enviado") and not args.forcar:
        print("NÃO FECHADO: o e-mail não foi enviado (ou foi dry-run). O estado só é atualizado após envio. Use --forcar para gravar assim mesmo.")
        return 1
    if ex.get("erro_coleta"):
        print("NÃO FECHADO: execução com falha de coleta — a janela não avança.")
        return 1
    proc = Processados(ARQ_PROCESSADOS)
    hoje = date.fromisoformat(ex["data_referencia"])
    proc.purgar(hoje)
    n = 0
    for p in ex["publicacoes"]:
        if p.get("ja_processada"):
            continue
        proc.registrar(Publicacao.from_dict({k: p.get(k) for k in Publicacao.__dataclass_fields__}), hoje)  # type: ignore[attr-defined]
        n += 1
    proc.salvar()
    gravar_json(ARQ_ULTIMA, {"executada_em": datetime.now().isoformat(timespec="seconds"),
                             "janela_fim": ex["janela"]["fim"], "publicacoes": n,
                             "prazos": sum(1 for x in ex.get("prazos", []) if x["status"] in ("LANCADO", "ATUALIZADO"))})
    print(f"FECHADO: {n} publicações registradas em processados.json; próxima janela começa em {br(ex['janela']['fim'])}.")
    return 0


# ---------------------------------------------------------------- etapa: descobrir-ids
def etapa_descobrir_ids(args: argparse.Namespace, config: dict[str, Any]) -> int:
    from trello import ClienteTrello
    cli = ClienteTrello(board_id=config["trello"].get("board_id"))
    r = cli.descobrir_ids(config)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    tcfg = config["trello"]
    tcfg["board_id"] = r["board"]["id"]
    tcfg["lista_destino_id"] = r["lista_destino_id"]
    tcfg["etiquetas"] = r["etiquetas"]
    tcfg["membros"] = r["membros"]
    shutil.copy(RAIZ / "config.yaml", RAIZ / "config.yaml.bak")
    with open(RAIZ / "config.yaml", "w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False, width=120)
    print("config.yaml atualizado (cópia anterior em config.yaml.bak — os comentários foram perdidos).")
    if r["membros_nao_encontrados"]:
        print(f"ATENÇÃO: membros não encontrados no quadro: {r['membros_nao_encontrados']}. Confirme os nomes.")
    return 0


# ---------------------------------------------------------------- main
def _terminal_utf8() -> None:
    """No Windows o console usa cp1252 e não imprime setas/acentos; força UTF-8 na saída."""
    for f in (sys.stdout, sys.stderr):
        try:
            f.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    _terminal_utf8()
    ap = argparse.ArgumentParser(description="prazos-djen — rotina de publicações e prazos")
    ap.add_argument("--etapa", choices=["coletar", "contar-e-lancar", "autoverificar", "email", "fechar", "descobrir-ids"])
    ap.add_argument("--dry-run", action="store_true", help="não cria cartões nem envia e-mail")
    ap.add_argument("--somente-email", action="store_true", help="não cria cartões; envia e-mail com aviso de validação")
    ap.add_argument("--data", help="força a janela (AAAA-MM-DD)")
    ap.add_argument("--ate", help="fim da janela forçada (AAAA-MM-DD), opcional")
    ap.add_argument("--erro-coleta", nargs="?", const="", help="com --etapa email: envia o e-mail de alerta de falha na coleta")
    ap.add_argument("--forcar", action="store_true", help="com --etapa fechar: grava estado mesmo sem e-mail enviado")
    ap.add_argument("--sem-legalcloud", action="store_true", help="não conferir prazos no site do Legalcloud")
    ap.add_argument("--para", help="com --etapa email: restringe o envio a estes destinatários (subconjunto do config.yaml), p.ex. para teste")
    args = ap.parse_args(argv)
    arq_log = configurar_log()
    config = carregar_config()
    log.info("etapa=%s dry_run=%s somente_email=%s data=%s log=%s", args.etapa, args.dry_run, args.somente_email, args.data, arq_log)
    etapas = {"coletar": etapa_coletar, "contar-e-lancar": etapa_contar_e_lancar, "autoverificar": etapa_autoverificar,
              "email": etapa_email, "fechar": etapa_fechar, "descobrir-ids": etapa_descobrir_ids}
    if args.etapa:
        return etapas[args.etapa](args, config)
    # execução completa
    rc = etapa_coletar(args, config)
    if rc:
        return rc
    pend = (ler_json(ARQ_PENDENTES) or {}).get("publicacoes", [])
    if pend and not ARQ_CLASSIFICADAS.exists():
        print("PAUSA: classifique estado/pendentes.json → estado/classificadas.json e rode as etapas contar-e-lancar, autoverificar, email e fechar.")
        return 3
    for nome in ("contar-e-lancar", "autoverificar", "email"):
        rc = etapas[nome](args, config)
        if nome == "email" and rc:
            return rc
    return etapa_fechar(args, config)


if __name__ == "__main__":
    sys.exit(main())
