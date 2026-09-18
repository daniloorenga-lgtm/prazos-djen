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
    """Devolve (assunto, html, texto). Ordem: resumo → alertas → publicações → prazos → pautas → sem providência → rodapé."""
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
    H: list[str] = []   # html (blocos dentro do container)

    # ---- paleta e estilos inline (compatíveis com Gmail/Outlook) ----
    C_TXT, C_MUTED, C_LINE, C_BG, C_CARD = "#1f2933", "#6b7280", "#e5e7eb", "#f3f4f6", "#ffffff"
    C_PRIM, C_PRIM_BG = "#1d4ed8", "#eff6ff"
    C_WARN_BG, C_WARN_TX, C_WARN_LN = "#fef9c3", "#713f12", "#fde68a"
    C_ERR_BG, C_ERR_TX, C_ERR_LN = "#fee2e2", "#7f1d1d", "#fca5a5"
    C_OK_TX = "#166534"
    FONT = "font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;"

    def sec(titulo: str, nota: str = "") -> None:
        T.append(f"\n== {titulo} ==")
        H.append(f"<h2 style='{FONT}font-size:15px;letter-spacing:.04em;text-transform:uppercase;color:{C_MUTED};"
                 f"margin:28px 0 10px;padding-bottom:6px;border-bottom:2px solid {C_LINE}'>{_e(titulo)}"
                 + (f" <span style='font-weight:normal;text-transform:none;letter-spacing:0'>{_e(nota)}</span>" if nota else "")
                 + "</h2>")

    def par(txt: str, cor: str = C_TXT) -> None:
        T.append(txt)
        H.append(f"<p style='{FONT}font-size:14px;line-height:1.5;color:{cor};margin:6px 0'>{_e(txt)}</p>")

    def lista(itens: list[str]) -> None:
        for i in itens:
            T.append(f" - {i}")
        H.append(f"<ul style='{FONT}font-size:14px;line-height:1.5;color:{C_TXT};margin:6px 0;padding-left:20px'>"
                 + "".join(f"<li style='margin:3px 0'>{_e(i)}</li>" for i in itens) + "</ul>")

    def aviso(txt: str, bg: str, tx: str, ln: str) -> None:
        T.append(txt)
        H.append(f"<div style='{FONT}font-size:14px;background:{bg};color:{tx};border:1px solid {ln};"
                 f"border-radius:6px;padding:10px 14px;margin:10px 0'><b>{_e(txt)}</b></div>")

    def badge(txt: str, bg: str, tx: str) -> str:
        return (f"<span style='display:inline-block;font-size:11px;font-weight:bold;letter-spacing:.03em;"
                f"background:{bg};color:{tx};border-radius:4px;padding:2px 7px;margin:0 4px 2px 0'>{_e(txt)}</span>")

    def badge_etiqueta(nome: str) -> str:
        if nome == "CONFERIR CONTAGEM":
            return badge(nome, C_WARN_BG, C_WARN_TX)
        if nome == "DÚVIDA DE CLASSIFICAÇÃO":
            return badge(nome, "#ede9fe", "#4c1d95")
        return badge(nome, C_BG, C_TXT)

    def num_proc(p: dict[str, Any]) -> str:
        return p.get("numero_processo") or p.get("numero_processo_original") or "não informado pela fonte"

    # ---- cabeçalho ----
    H.append(f"<div style='{FONT}padding:22px 24px;background:{C_PRIM};color:#ffffff;border-radius:8px 8px 0 0'>"
             f"<div style='font-size:12px;letter-spacing:.08em;text-transform:uppercase;opacity:.85'>Muriel Advogados · rotina de prazos</div>"
             f"<div style='font-size:22px;font-weight:bold;margin-top:4px'>Publicações e prazos</div>"
             f"<div style='font-size:15px;margin-top:2px;opacity:.95'>{_e(br(ex['data_referencia']))}</div></div>")
    H.append(f"<div style='padding:20px 24px 24px;background:{C_CARD}'>")

    if flags.get("somente_email"):
        aviso("MODO DE VALIDAÇÃO — nenhum cartão criado", C_WARN_BG, C_WARN_TX, C_WARN_LN)
    if flags.get("dry_run"):
        aviso("DRY-RUN — nenhum cartão criado; e-mail não enviado (apenas gerado)", C_WARN_BG, C_WARN_TX, C_WARN_LN)

    # 1. resumo (texto simples + três indicadores)
    resumo = f"{n_pub} publicações · {n_prazos} prazos lançados · {n_alertas} alertas"
    T.append(resumo)
    H.append(f"<!-- {_e(resumo)} -->")

    def tile(n: int, rotulo: str, cor: str) -> str:
        return (f"<td width='33%' style='padding:0 4px'><div style='{FONT}background:{C_BG};border-radius:8px;padding:12px 8px;text-align:center'>"
                f"<div style='font-size:26px;font-weight:bold;color:{cor}'>{n}</div>"
                f"<div style='font-size:12px;color:{C_MUTED};text-transform:uppercase;letter-spacing:.04em'>{_e(rotulo)}</div></div></td>")
    H.append("<table role='presentation' width='100%' cellspacing='0' cellpadding='0' style='margin:6px 0 4px'><tr>"
             + tile(n_pub, "publicações", C_TXT) + tile(n_prazos, "prazos lançados", C_PRIM)
             + tile(n_alertas, "alertas", C_ERR_TX if n_alertas else C_OK_TX) + "</tr></table>")
    if erro_coleta:
        aviso(f"A coleta no DJEN falhou duas vezes: {erro_coleta}. Nenhuma publicação foi processada; "
              f"repetir manualmente com `python src/rodar.py --data {ex['data_referencia']}`.", C_ERR_BG, C_ERR_TX, C_ERR_LN)

    # 2. alertas
    if nao_lancados:
        sec("Alertas — NÃO LANÇADOS NO TRELLO (lançar manualmente)")
        for p in nao_lancados:
            aviso(f"{p['titulo']} · fatal {br(p['data_final'])} · lembrete {br(p['data_lembrete'])} · "
                  f"processo {p['numero_processo'] or 'não informado pela fonte'} · {p['dias']} dias"
                  f"{' corridos' if p['dias_corridos'] else ' úteis'} · intimado: {', '.join(p['intimados'])} · "
                  f"etiquetas: {', '.join(p['etiquetas'])} · motivo: {p['detalhe_status']}", C_ERR_BG, C_ERR_TX, C_ERR_LN)
    if alertas:
        sec("Alertas")
        lista(alertas)

    # 3. publicações do dia (um cartão por publicação, com os advogados intimados)
    sec("Publicações do dia", f"({n_pub})" if n_pub else "")
    if pubs_relatadas:
        for p in pubs_relatadas:
            derivados = [x for x in prazos if x["hash_publicacao"] == p["hash"]]
            intim = ", ".join(p.get("intimados") or ["(intimado não identificado)"])
            cat = p.get("categoria", "SEM CATEGORIA")
            dstr = "; ".join(f"{x['ato']} {x['dias']}d → {br(x['data_final'], False)} [{x['status']}]" for x in derivados) or "nenhum"
            T.append(f" - {num_proc(p)} · {p['tribunal']} / {p['orgao']} · disp. {br(p['data_disponibilizacao'])} · {cat} · "
                     f"intimados: {intim} · {p.get('resumo') or '(sem resumo)'} · prazos: {dstr}"
                     + (" · REPUBLICAÇÃO" if p.get("republicacao") else "")
                     + (f" · dúvida: {p['duvida']}" if p.get("duvida") else ""))
            cat_badge = badge(cat, C_PRIM_BG, C_PRIM) if cat not in ("INDETERMINADA", "SEM CATEGORIA") else badge(cat, C_ERR_BG, C_ERR_TX)
            card = [f"<div style='{FONT}border:1px solid {C_LINE};border-radius:8px;padding:14px 16px;margin:10px 0'>",
                    f"<div style='font-size:15px;font-weight:bold;color:{C_TXT}'>{_e(num_proc(p))}"
                    + (f" &nbsp;{badge('REPUBLICAÇÃO', C_WARN_BG, C_WARN_TX)}" if p.get("republicacao") else "") + "</div>",
                    f"<div style='font-size:13px;color:{C_MUTED};margin:2px 0 8px'>{_e(p['tribunal'])} · {_e(p['orgao'])}<br>"
                    f"Disponibilização: {_e(br(p['data_disponibilizacao']))} · Intimados: {_e(intim)}</div>",
                    f"<div style='margin-bottom:8px'>{cat_badge}</div>"]
            if derivados:
                card.append(f"<table role='presentation' cellspacing='0' cellpadding='0' style='font-size:13px;color:{C_TXT};margin:4px 0'>")
                for x in derivados:
                    card.append(f"<tr><td style='padding:2px 10px 2px 0;color:{C_MUTED}'>{_e(x['ato'])}</td>"
                                f"<td style='padding:2px 10px 2px 0'>{x['dias']} dias{' corridos' if x.get('dias_corridos') else ' úteis'}</td>"
                                f"<td style='padding:2px 0;font-weight:bold'>{_e(br(x['data_final']))}</td></tr>")
                card.append("</table>")
            elif p.get("resumo"):
                card.append(f"<div style='font-size:13px;color:{C_TXT};line-height:1.5'>{_e(p['resumo'])}</div>")
            if p.get("duvida") and p.get("duvida") != p.get("resumo"):
                card.append(f"<div style='font-size:13px;line-height:1.5;background:{C_WARN_BG};color:{C_WARN_TX};"
                            f"border-left:3px solid {C_WARN_LN};padding:8px 10px;margin-top:10px'><b>Dúvida:</b> {_e(p['duvida'])}</div>")
            # texto integral da publicação (regra: o conteúdo consta do e-mail; cópia literal, sem resumir nem cortar)
            texto_pub = p.get("texto_integral") or "não informado pela fonte"
            card.append(f"<div style='font-size:11px;letter-spacing:.04em;text-transform:uppercase;color:{C_MUTED};margin:12px 0 4px'>Texto da publicação</div>"
                        f"<div style='font-size:12.5px;line-height:1.55;color:{C_TXT};background:{C_BG};border-radius:6px;"
                        f"padding:10px 12px;white-space:pre-wrap;word-break:break-word'>{_e(texto_pub)}</div>")
            T.append(f"   Texto da publicação: {texto_pub}")
            card.append("</div>")
            H.append("".join(card))
        par("Os recortes também seguem no anexo recortes-AAAA-MM-DD.txt.", C_MUTED)
    else:
        par("Nenhuma publicação na janela.")

    # 4. prazos lançados
    sec("Prazos lançados no Trello", "(simulação)" if dry else "")
    ordenados = sorted(lancados + dry, key=lambda p: p["data_final"])
    if ordenados:
        cab = ["Prazo fatal", "Lembrete", "Processo", "Cliente", "Ato", "Advogado intimado", "Cartão", "Etiquetas", "Legalcloud"]
        T.append(" | ".join(cab))
        th = (f"style='{FONT}font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:{C_MUTED};"
              f"text-align:left;padding:8px 8px;border-bottom:2px solid {C_LINE}'")
        rows = []
        for i, p in enumerate(ordenados):
            url = (p.get("cartao_fatal") or {}).get("url") or ""
            cartao_txt = url or ("(não criado — simulação)" if p["status"] == "DRY_RUN" else "")
            lc_txt = _legalcloud_col(p)
            T.append(" | ".join([br(p["data_final"]), br(p["data_lembrete"]), p["numero_processo"] or "não informado pela fonte",
                                 p["cliente"], p["ato"], ", ".join(p["intimados"]), cartao_txt, ", ".join(p["etiquetas"]), lc_txt]))
            bg = C_CARD if i % 2 == 0 else "#f9fafb"
            td = (f"style='{FONT}font-size:13px;color:{C_TXT};padding:8px 8px;border-bottom:1px solid {C_LINE};"
                  f"vertical-align:top;background:{bg}'")
            lc_cor = C_ERR_TX if lc_txt.startswith("DIVERGE") else (C_OK_TX if lc_txt.startswith("confere") else C_MUTED)
            cartao_html = (f"<a href='{_e(url)}' style='color:{C_PRIM}'>abrir cartão</a>" if url
                           else f"<span style='color:{C_MUTED}'>{_e(cartao_txt)}</span>")
            dia_semana = DIAS[date.fromisoformat(p["data_final"][:10]).weekday()]
            rows.append("<tr>"
                        f"<td {td}><b>{_e(br(p['data_final'], False))}</b><br><span style='color:{C_MUTED};font-size:11px'>{_e(dia_semana)}</span></td>"
                        f"<td {td}>{_e(br(p['data_lembrete'], False))}</td>"
                        f"<td {td}><b>{_e(p['cliente'])}</b><br><span style='color:{C_MUTED};font-size:12px'>{_e(p['numero_processo'] or 'não informado pela fonte')}</span></td>"
                        f"<td {td}>{_e(p['ato'])}<br><span style='color:{C_MUTED};font-size:12px'>intimado: {_e(', '.join(p['intimados']))}</span></td>"
                        f"<td {td}>{''.join(badge_etiqueta(e) for e in p['etiquetas'])}</td>"
                        f"<td {td}>{cartao_html}<br><span style='color:{lc_cor};font-size:12px'>Legalcloud: {_e(lc_txt)}</span></td>"
                        "</tr>")
        H.append("<table role='presentation' width='100%' cellspacing='0' cellpadding='0' style='border-collapse:collapse;margin:4px 0'>"
                 f"<tr><th {th}>Prazo fatal</th><th {th}>Lembrete</th><th {th}>Cliente / processo</th><th {th}>Ato</th>"
                 f"<th {th}>Etiquetas</th><th {th}>Cartão</th></tr>"
                 + "".join(rows) + "</table>")
        duplicados = [p for p in prazos if p["status"] == "DUPLICADO"]
        if duplicados:
            par("Prazos já existentes no quadro (não recriados):")
            lista([f"{p['titulo']} — {p['detalhe_status']}" for p in duplicados])
    else:
        par("Nenhum prazo lançado.")

    # 5. pautas e audiências
    sec("Pautas e audiências")
    pa = [p for p in pubs_relatadas if p.get("categoria") in ("PAUTA_JULGAMENTO", "AUDIENCIA")]
    if pa:
        lista([f"{num_proc(p)} · {p['tribunal']} · {p['categoria']} · "
               f"{p.get('resumo') or ''}" + (f" · {p['duvida']}" if p.get('duvida') else "") for p in pa])
    else:
        par("Nenhuma.", C_MUTED)

    # 6. sem providência
    sec("Sem providência")
    sp = [p for p in pubs_relatadas if p.get("categoria") == "MERA_CIENCIA"
          or (p.get("republicacao") and not any(x["hash_publicacao"] == p["hash"] for x in prazos))
          or p.get("contraparte")]
    if sp:
        lista([f"{num_proc(p)} · {p['tribunal']} · "
               f"{'republicação sem alteração' if p.get('republicacao') else p.get('categoria')} · {p.get('resumo') or ''}" for p in sp])
    else:
        par("Nenhuma.", C_MUTED)

    # 7. rodapé
    T.append("\n== Rodapé ==")
    cal = ex.get("calendario", {})
    fontes = ex.get("fontes_status", {})
    rod = [
        f"Janela de busca: {br(ex['janela']['inicio'])} a {br(ex['janela']['fim'])} ({ex['janela'].get('motivo', '')})",
        "Fontes: " + ("; ".join(f"{k}: {v}" for k, v in fontes.items()) or "nenhuma consultada"),
        f"Execução: {ex.get('iniciada_em', '')} → {datetime.now().strftime('%d/%m/%Y %H:%M')}",
        f"Calendário forense (Legalcloud): última extração {cal.get('extraido_em') or 'nunca'}"
        + (f" ({cal['idade_dias']} dias)" if cal.get("idade_dias") is not None else ""),
        f"Prazos conferidos no Legalcloud nesta execução: {ex.get('conferidos_legalcloud', 0)}"
        + (f" · {ex['legalcloud']['divergencias']} divergência(s)" if ex.get("legalcloud", {}).get("divergencias") else "")
        + (f" · site indisponível: {ex['legalcloud']['indisponivel']}" if ex.get("legalcloud", {}).get("indisponivel") else ""),
        f"Publicações já tratadas em execução anterior (não repetidas): {sum(1 for p in pubs if p.get('ja_processada'))}",
    ]
    for i in rod:
        T.append(f" - {i}")
    H.append(f"<div style='{FONT}font-size:11px;line-height:1.6;color:{C_MUTED};border-top:1px solid {C_LINE};"
             f"margin-top:28px;padding-top:12px'>" + "<br>".join(_e(i) for i in rod) + "</div>")
    H.append("</div>")  # fecha o corpo do cartão

    texto = "\n".join(T)
    html = (f"<html><body style='margin:0;padding:16px;background:{C_BG}'>"
            f"<table role='presentation' width='100%' cellspacing='0' cellpadding='0'><tr><td align='center'>"
            f"<div style='max-width:760px;margin:0 auto;text-align:left;border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,.08)'>"
            + "".join(H) + "</div></td></tr></table></body></html>")
    return assunto, html, texto


def _legalcloud_col(p: dict[str, Any]) -> str:
    lc = p.get("legalcloud")
    if not lc:
        return "não conferido" if p.get("conferir") else "—"
    if lc.get("data_site") is None:
        return f"sem resultado ({lc.get('observacao', '')})"
    if lc.get("divergiu"):
        return f"DIVERGE: site {br(lc['data_site'], False)} × local {br(p.get('data_final_local'), False)}"
    return f"confere ({br(lc['data_site'], False)})"


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
