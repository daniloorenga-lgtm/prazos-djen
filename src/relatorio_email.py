"""Montagem e envio do e-mail de relatório via Gmail API (§6 da especificação).

Nome do módulo: `relatorio_email` (e não `email.py`) porque `python src/rodar.py` coloca `src/`
no início do sys.path e um `email.py` local sombrearia o pacote `email` da biblioteca padrão,
usado por `http.client`, `requests` e pela própria Gmail API.
"""
from __future__ import annotations

import base64
import html as html_mod
import logging
import os
from datetime import date, datetime
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email import encoders
from typing import Any

log = logging.getLogger("email")

DIAS = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]


def br(iso: str | None, com_dia: bool = True) -> str:
    if not iso or len(iso) < 10:
        return iso or "não informado pela fonte"
    try:
        d = date.fromisoformat(iso[:10])
    except ValueError:
        return iso
    s = d.strftime("%d/%m/%Y")
    return f"{s} ({DIAS[d.weekday()]})" if com_dia else s


def _e(s: Any) -> str:
    return html_mod.escape(str(s if s is not None else ""))


def montar_email(ex: dict[str, Any]) -> tuple[str, str, str]:
    """Devolve (assunto, html, texto). Segue a ordem do §6."""
    hoje = date.fromisoformat(ex["data_referencia"])
    pubs: list[dict[str, Any]] = ex.get("publicacoes", [])
    prazos: list[dict[str, Any]] = ex.get("prazos", [])
    alertas: list[str] = list(ex.get("alertas", []))
    flags = ex.get("flags", {})
    lancados = [p for p in prazos if p["status"] in ("LANCADO", "ATUALIZADO")]
    nao_lancados = [p for p in prazos if p["status"] == "NAO_LANCADO"]
    dry = [p for p in prazos if p["status"] in ("DRY_RUN",)]
    erro_coleta = ex.get("erro_coleta")

    pubs_relatadas = [p for p in pubs if not p.get("ja_processada")]
    n_pub = len(pubs_relatadas)
    n_prazos = len(lancados) + len(dry)
    n_alertas = len(alertas) + len(nao_lancados)

    critico = bool(nao_lancados or erro_coleta or ex.get("erros_coleta"))
    if erro_coleta:
        assunto = f"[ATENÇÃO] Publicações e prazos — {br(ex['data_referencia'])} — FALHA NA COLETA"
    elif n_pub == 0:
        assunto = f"Publicações e prazos — {hoje.strftime('%d/%m/%Y')} — sem publicações"
        if ex.get("erros_coleta"):
            assunto = "[ATENÇÃO] " + assunto
    else:
        assunto = f"Publicações e prazos — {br(ex['data_referencia'])}"
        if critico:
            assunto = "[ATENÇÃO] " + assunto

    T: list[str] = []   # texto
    H: list[str] = []   # html

    def sec(titulo: str) -> None:
        T.append(f"\n== {titulo} ==")
        H.append(f"<h3>{_e(titulo)}</h3>")

    def par(txt: str) -> None:
        T.append(txt)
        H.append(f"<p>{_e(txt)}</p>")

    def lista(itens: list[str]) -> None:
        for i in itens:
            T.append(f" - {i}")
        H.append("<ul>" + "".join(f"<li>{_e(i)}</li>" for i in itens) + "</ul>")

    if flags.get("somente_email"):
        par("MODO DE VALIDAÇÃO — nenhum cartão criado")
        H[-1] = "<p style='background:#fff3cd;padding:6px'><b>MODO DE VALIDAÇÃO — nenhum cartão criado</b></p>"
    if flags.get("dry_run"):
        par("DRY-RUN — nenhum cartão criado; e-mail não enviado (apenas gerado)")

    # 1. resumo
    par(f"{n_pub} publicações · {n_prazos} prazos lançados · {n_alertas} alertas")
    if erro_coleta:
        par(f"A coleta no DJEN falhou duas vezes: {erro_coleta}. Nenhuma publicação foi processada; "
            f"repetir manualmente com `python src/rodar.py --data {ex['data_referencia']}`.")

    # 2. alertas
    if nao_lancados:
        sec("Alertas — NÃO LANÇADOS NO TRELLO (lançar manualmente)")
        itens = []
        for p in nao_lancados:
            itens.append(f"{p['titulo']} · fatal {br(p['data_final'])} · lembrete {br(p['data_lembrete'])} · "
                         f"processo {p['numero_processo'] or 'não informado pela fonte'} · {p['dias']} dias"
                         f"{' corridos' if p['dias_corridos'] else ' úteis'} · intimado: {', '.join(p['intimados'])} · "
                         f"etiquetas: {', '.join(p['etiquetas'])} · motivo: {p['detalhe_status']}")
        lista(itens)
    if alertas:
        sec("Alertas")
        lista(alertas)

    # 3. prazos lançados
    sec("Prazos lançados no Trello" + (" (simulação)" if dry else ""))
    ordenados = sorted(lancados + dry, key=lambda p: p["data_final"])
    if ordenados:
        cab = ["Prazo fatal", "Lembrete", "Processo", "Cliente", "Ato", "Advogado intimado", "Cartão", "Etiquetas"]
        linhas = []
        for p in ordenados:
            url = (p.get("cartao_fatal") or {}).get("url") or ("(não criado — simulação)" if p["status"] == "DRY_RUN" else "")
            linhas.append([br(p["data_final"]), br(p["data_lembrete"]), p["numero_processo"] or "não informado pela fonte",
                           p["cliente"], p["ato"], ", ".join(p["intimados"]), url, ", ".join(p["etiquetas"])])
        T.append(" | ".join(cab))
        for l in linhas:
            T.append(" | ".join(l))
        H.append("<table border='1' cellpadding='4' style='border-collapse:collapse;font-size:13px'><tr>"
                 + "".join(f"<th>{_e(c)}</th>" for c in cab) + "</tr>"
                 + "".join("<tr>" + "".join(
                     (f"<td><a href='{_e(c)}'>{_e(c)}</a></td>" if str(c).startswith("http") else f"<td>{_e(c)}</td>")
                     for c in l) + "</tr>" for l in linhas) + "</table>")
        duplicados = [p for p in prazos if p["status"] == "DUPLICADO"]
        if duplicados:
            par("Prazos já existentes no quadro (não recriados):")
            lista([f"{p['titulo']} — {p['detalhe_status']}" for p in duplicados])
    else:
        par("Nenhum prazo lançado.")

    # 4. publicações por advogado
    sec("Publicações do dia, por advogado")
    if pubs_relatadas:
        por_adv: dict[str, list[dict[str, Any]]] = {}
        for p in pubs_relatadas:
            for a in (p.get("intimados") or ["(intimado não identificado)"]):
                por_adv.setdefault(a, []).append(p)
        for adv in sorted(por_adv):
            T.append(f"\n[{adv}]")
            H.append(f"<h4>{_e(adv)}</h4>")
            itens = []
            for p in por_adv[adv]:
                derivados = [x for x in prazos if x["hash_publicacao"] == p["hash"]]
                dstr = "; ".join(f"{x['ato']} {x['dias']}d → {br(x['data_final'], False)} [{x['status']}]" for x in derivados) or "nenhum"
                itens.append(f"{p['numero_processo'] or p['numero_processo_original']} · {p['tribunal']} / {p['orgao']} · "
                             f"disp. {br(p['data_disponibilizacao'])} · {p.get('categoria', 'SEM CATEGORIA')} · "
                             f"{p.get('resumo') or '(sem resumo)'} · prazos: {dstr}"
                             + (" · REPUBLICAÇÃO" if p.get("republicacao") else "")
                             + (f" · dúvida: {p['duvida']}" if p.get("duvida") else ""))
            lista(itens)
        par("Texto integral de cada publicação: no anexo recortes-AAAA-MM-DD.txt.")
    else:
        par("Nenhuma publicação na janela.")

    # 5. pautas e audiências
    sec("Pautas e audiências")
    pa = [p for p in pubs_relatadas if p.get("categoria") in ("PAUTA_JULGAMENTO", "AUDIENCIA")]
    if pa:
        lista([f"{p['numero_processo'] or p['numero_processo_original']} · {p['tribunal']} · {p['categoria']} · "
               f"{p.get('resumo') or ''}" + (f" · {p['duvida']}" if p.get('duvida') else "") for p in pa])
    else:
        par("Nenhuma.")

    # 6. sem providência
    sec("Sem providência")
    sp = [p for p in pubs_relatadas if p.get("categoria") == "MERA_CIENCIA"
          or (p.get("republicacao") and not any(x["hash_publicacao"] == p["hash"] for x in prazos))
          or p.get("contraparte")]
    if sp:
        lista([f"{p['numero_processo'] or p['numero_processo_original']} · {p['tribunal']} · "
               f"{'republicação sem alteração' if p.get('republicacao') else p.get('categoria')} · {p.get('resumo') or ''}" for p in sp])
    else:
        par("Nenhuma.")

    # 7. rodapé
    sec("Rodapé")
    cal = ex.get("calendario", {})
    fontes = ex.get("fontes_status", {})
    rod = [
        f"Janela de busca: {br(ex['janela']['inicio'])} a {br(ex['janela']['fim'])} ({ex['janela'].get('motivo', '')})",
        "Fontes: " + ("; ".join(f"{k}: {v}" for k, v in fontes.items()) or "nenhuma consultada"),
        f"Execução: {ex.get('iniciada_em', '')} → {datetime.now().strftime('%d/%m/%Y %H:%M')}",
        f"Calendário forense (Legalcloud): última extração {cal.get('extraido_em') or 'nunca'}"
        + (f" ({cal['idade_dias']} dias)" if cal.get("idade_dias") is not None else ""),
        f"Prazos conferidos no Legalcloud nesta execução: {ex.get('conferidos_legalcloud', 0)}",
        f"Publicações já tratadas em execução anterior (não repetidas): {sum(1 for p in pubs if p.get('ja_processada'))}",
    ]
    lista(rod)

    texto = "\n".join(T)
    html = ("<html><body style='font-family:Arial,sans-serif;font-size:14px'>"
            + "".join(H) + "</body></html>")
    return assunto, html, texto


def anexo_recortes(ex: dict[str, Any]) -> str:
    partes = []
    for p in ex.get("publicacoes", []):
        if p.get("ja_processada"):
            continue
        partes.append("=" * 78)
        partes.append(f"Processo: {p['numero_processo'] or p['numero_processo_original']} · {p['tribunal']} · {p['orgao']}")
        partes.append(f"Disponibilização: {br(p['data_disponibilizacao'])} · Fonte: {p['fonte']} {p['id_fonte']} · Link: {p.get('link', '')}")
        partes.append(f"Categoria: {p.get('categoria', '')} · Intimados: {', '.join(p.get('intimados', []))}")
        partes.append("-" * 78)
        partes.append(p["texto_integral"])
        partes.append("")
    return "\n".join(partes)


def _servico_gmail():
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    cid, csec, rt = (os.environ.get("GMAIL_CLIENT_ID"), os.environ.get("GMAIL_CLIENT_SECRET"),
                     os.environ.get("GMAIL_REFRESH_TOKEN"))
    if not (cid and csec and rt):
        raise RuntimeError("GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET e GMAIL_REFRESH_TOKEN são obrigatórios (.env)")
    creds = Credentials(None, refresh_token=rt, client_id=cid, client_secret=csec,
                        token_uri="https://oauth2.googleapis.com/token",
                        scopes=["https://www.googleapis.com/auth/gmail.send"])
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def enviar(remetente: str, destinatarios: list[str], assunto: str, html: str, texto: str,
           anexo_nome: str | None = None, anexo_conteudo: str | None = None) -> dict[str, Any]:
    msg = MIMEMultipart("mixed")
    msg["From"] = remetente
    msg["To"] = ", ".join(destinatarios)
    msg["Subject"] = assunto
    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(texto, "plain", "utf-8"))
    alt.attach(MIMEText(html, "html", "utf-8"))
    msg.attach(alt)
    if anexo_nome and anexo_conteudo is not None:
        parte = MIMEBase("text", "plain", charset="utf-8")
        parte.set_payload(anexo_conteudo.encode("utf-8"))
        encoders.encode_base64(parte)
        parte.add_header("Content-Disposition", "attachment", filename=anexo_nome)
        msg.attach(parte)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
    servico = _servico_gmail()
    resp = servico.users().messages().send(userId="me", body={"raw": raw}).execute()
    log.info("e-mail enviado: id=%s", resp.get("id"))
    return {"id": resp.get("id"), "threadId": resp.get("threadId")}
