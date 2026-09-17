"""Conferência de prazos na calculadora do Legalcloud (§0 uso_2 e §4.2 da especificação).

Navegador headless (Playwright + Chromium). Credenciais SÓ de LEGALCLOUD_USER / LEGALCLOUD_PASS.
Usa a calculadora "Prazos DJEN/DJE" (https://app.legalcloud.com.br/calculadora/prazo-djen-dje/), que
recebe a DATA DE DISPONIBILIZAÇÃO e simula dia a dia. Campos observados em 17/09/2026 (CAMPOS_DJEN):
meio_comunicacao (DJE/DJEN), data_disponibilizacao, dias, codigo (Novo CPC/CPP/Antiga CLT/Juizado
Especial/CLT 2017), tribunal (siglas; TRF-1..6), tipo_processo (Físico/Eletrônico), select sem nome
"sistema" (eSAJ/Eproc, aparece para alguns tribunais), instancia, incluir (suspensões municipais),
botão #primeiro_calculo. O resultado é lido do texto "Simulação do prazo processual" (dias numerados).
O login (labels E-mail/Senha/Entrar) usa a lista de candidatos `campos`.

Calibração (primeiro uso):  python src/legalcloud.py --explorar
  → faz login, abre a calculadora e grava em logs/legalcloud/ a captura de tela, o HTML e um
    inventário de campos (labels, names, selects com opções, botões). Ajuste `legalcloud.campos`
    no config.yaml se algum candidato não bater.
Teste manual:               python src/legalcloud.py --testar TJSP 2026-09-17 15   (data = DISPONIBILIZAÇÃO)
Fonte simulada (validação): PRAZOS_LEGALCLOUD_MOCK=arquivo.json  ({"TJSP|2026-09-17|15|uteis": "2026-10-09", "*": "igual"}; chave = tribunal|D0|dias|uteis/corridos)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger("legalcloud")

RE_DATA = re.compile(r"\b(\d{2})/(\d{2})/(\d{4})\b")
PALAVRAS_RESULTADO = ("prazo final", "data final", "vencimento", "término", "termino", "termina", "fim do prazo", "dies ad quem", "último dia", "ultimo dia")

CAMPOS_PADRAO: dict[str, list[str]] = {
    "usuario": ["label=E-mail", "label=Email", "label=Usuário", "css=input[type=email]", "css=input[name=email]", "css=input[name=username]", "css=input[name=login]"],
    "senha": ["label=Senha", "css=input[type=password]"],
    "entrar": ["role=button:Entrar", "role=button:Login", "role=button:Acessar", "css=button[type=submit]", "css=input[type=submit]"],
    "tribunal": ["label=Tribunal", "css=select[name*=tribunal i]", "css=[id*=tribunal i]"],
    "tipo_processo": ["label=Tipo de processo", "label=Tipo do processo", "label=Natureza", "css=select[name*=tipo i]"],
    "data_publicacao": ["label=Data da publicação", "label=Data de publicação", "label=Publicação", "label=Data da disponibilização", "label=Data inicial", "css=input[name*=public i]", "css=input[name*=data i]"],
    "dias": ["label=Prazo (dias)", "label=Quantidade de dias", "label=Dias", "label=Prazo", "css=input[name*=dias i]", "css=input[name*=prazo i]"],
    "contagem_uteis": ["label=Dias úteis", "text=Dias úteis", "css=input[value*=util i]"],
    "contagem_corridos": ["label=Dias corridos", "text=Dias corridos", "css=input[value*=corrid i]"],
    "calcular": ["role=button:Calcular", "role=button:Contar", "css=button[type=submit]"],
    "resultado": ["css=#resultado", "css=.resultado", "css=[class*=resultado i]", "css=[id*=resultado i]", "text=/prazo final|data final|vencimento|t[ée]rmino/i"],
}


CAMPOS_DJEN: dict[str, str] = {
    "meio": "select[name=meio_comunicacao]",
    "data": "input[name=data_disponibilizacao]",
    "dias": "input[name=dias]",
    "codigo": "select[name=codigo]",
    "tribunal": "select[name=tribunal]",
    "tipo_processo": "select[name=tipo_processo]",
    "sistema": "select:not([name])",
    "instancia": "select[name=instancia]",
    "incluir": "select[name=incluir]",
    "simular": "#primeiro_calculo",
}

RE_LINHA_DATA = re.compile(r"^(\d{2}/\d{2}/\d{4})\s*-\s*(.*)$")


def interpretar_simulacao(texto: str, dias: int) -> dict[str, Any]:
    """Lê o texto da simulação do Legalcloud: dias numerados (contados) e dias desconsiderados.
    Devolve data_final (dia numerado == dias, senão o último numerado), dia_1, dia_do_comeco,
    desconsiderados [(data, motivo)] sem fins de semana, e n_contados."""
    linhas = [l.strip() for l in texto.splitlines()]
    ini = next((i for i, l in enumerate(linhas) if l.lower().startswith("simulação do prazo processual")), None)
    if ini is None:
        return {"ok": False, "motivo": "texto sem 'Simulação do prazo processual'"}
    contados: list[tuple[int, date]] = []
    desconsiderados: list[tuple[date, str]] = []
    comeco: date | None = None
    n_pendente: int | None = None
    i = ini + 1
    while i < len(linhas):
        l = linhas[i]
        if l.lower().startswith("legenda"):
            break
        if re.fullmatch(r"\d+", l):
            n_pendente = int(l)
            i += 1
            continue
        m = RE_LINHA_DATA.match(l)
        if m:
            d = date(int(m.group(1)[6:]), int(m.group(1)[3:5]), int(m.group(1)[:2]))
            motivo = m.group(2).strip()
            if not motivo and i + 1 < len(linhas) and not RE_LINHA_DATA.match(linhas[i + 1]) and not re.fullmatch(r"\d+", linhas[i + 1]):
                motivo = linhas[i + 1].strip()
                i += 1
            if n_pendente is not None:
                contados.append((n_pendente, d))
                n_pendente = None
            else:
                if "dia do começo" in motivo.lower() or "dia do comeco" in motivo.lower():
                    comeco = d
                elif "final de semana" not in motivo.lower():
                    desconsiderados.append((d, motivo))
        i += 1
    if not contados:
        return {"ok": False, "motivo": "simulação sem dias contados"}
    final = next((d for n, d in contados if n == dias), None) or contados[-1][1]
    return {"ok": True, "data_final": final, "dia_1": contados[0][1], "dia_do_comeco": comeco,
            "n_contados": contados[-1][0], "desconsiderados": desconsiderados}


def mensagem_de_aviso(texto: str) -> str:
    """Texto do modal de aviso do site (entre o botão 'Simular' e 'OK'), quando não há simulação.
    Ex.: 'Quantidade de dias inválida! Para simular prazos com data de evento 60 dias no futuro …'"""
    linhas = [l.strip() for l in texto.splitlines() if l.strip()]
    try:
        i = max(k for k, l in enumerate(linhas) if l == "Simular")
    except ValueError:
        return ""
    corpo = []
    for l in linhas[i + 1:i + 8]:
        if l == "OK":
            break
        if l != "!":
            corpo.append(l)
    return " ".join(corpo)[:300]


def sigla_para_site(tribunal: str) -> str:
    t = (tribunal or "").upper().strip()
    m = re.fullmatch(r"TRF-?(\d)", t)
    return f"TRF-{m.group(1)}" if m else t


@dataclass
class ResultadoConferencia:
    data_site: date | None
    confere: bool | None            # None = não foi possível conferir
    observacao: str = ""
    captura: str | None = None
    texto_resultado: str = ""


@dataclass
class Divergencia:
    data_local: date
    data_site: date
    prevalece: date = field(init=False)

    def __post_init__(self) -> None:
        self.prevalece = min(self.data_local, self.data_site)


def resolver_conferencia(data_local: date, res: ResultadoConferencia) -> tuple[date, str, bool]:
    """§4.2: se divergir, prevalece a data mais curta como fatal. Devolve (data_final, linha do cartão, divergiu)."""
    hoje = date.today().strftime("%d/%m")
    if res.data_site is None or res.confere is None:
        return data_local, f"Legalcloud: {hoje} — não conferido ({res.observacao or 'sem resultado'})", False
    if res.confere:
        return data_local, f"Legalcloud: {hoje} — confere ({res.data_site.strftime('%d/%m/%Y')})", False
    d = Divergencia(data_local, res.data_site)
    return d.prevalece, (f"Legalcloud: {hoje} — DIVERGE (site: {res.data_site.strftime('%d/%m/%Y')}; contagem local: "
                         f"{data_local.strftime('%d/%m/%Y')}) — prevalece a mais curta: {d.prevalece.strftime('%d/%m/%Y')}"), True


def extrair_data_resultado(texto: str, apos: date | None = None) -> date | None:
    """Acha a data do resultado num texto: prefere a primeira data após uma palavra-chave;
    senão, a última data posterior a `apos`."""
    if not texto:
        return None
    t = texto.lower()
    candidatas: list[tuple[int, date]] = []
    for m in RE_DATA.finditer(texto):
        try:
            candidatas.append((m.start(), date(int(m.group(3)), int(m.group(2)), int(m.group(1)))))
        except ValueError:
            continue
    if not candidatas:
        return None
    for palavra in PALAVRAS_RESULTADO:
        i = t.find(palavra)
        if i >= 0:
            depois = [d for pos, d in candidatas if pos >= i and (apos is None or d > apos)]
            if depois:
                return depois[0]
    validas = [d for _, d in candidatas if apos is None or d > apos]
    return validas[-1] if validas else None


class ConferidorMock:
    """Simula o site: mapa chave→data ISO; "*": "igual" faz o site concordar com a contagem local."""

    def __init__(self, caminho: Path):
        with open(caminho, encoding="utf-8") as f:
            self.mapa = json.load(f)
        self.chamadas = 0
        log.warning("USANDO LEGALCLOUD SIMULADO: %s", caminho)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def conferir(self, tribunal: str, tipo_processo: str, data_publicacao: date, dias: int,
                 dias_corridos: bool, data_local: date | None = None, **_: Any) -> ResultadoConferencia:
        self.chamadas += 1
        chave = f"{tribunal}|{data_publicacao.isoformat()}|{dias}|{'corridos' if dias_corridos else 'uteis'}"
        v = self.mapa.get(chave, self.mapa.get("*"))
        if v is None:
            return ResultadoConferencia(None, None, "simulado: sem resposta")
        if v == "igual":
            return ResultadoConferencia(data_local, True if data_local else None, "simulado")
        d = date.fromisoformat(v)
        return ResultadoConferencia(d, (d == data_local) if data_local else None, "simulado")


class ConferidorLegalcloud:
    def __init__(self, cfg: dict[str, Any], pasta_logs: Path, headless: bool = True):
        self.cfg = cfg
        self.campos = {**CAMPOS_PADRAO, **(cfg.get("campos") or {})}
        self.pasta = pasta_logs / "legalcloud"
        self.pasta.mkdir(parents=True, exist_ok=True)
        self.headless = headless
        self.timeout = int(cfg.get("timeout_segundos", 45)) * 1000
        self.usuario = os.environ.get("LEGALCLOUD_USER", "")
        self.senha = os.environ.get("LEGALCLOUD_PASS", "")
        self._pw = None
        self.browser = None
        self.page = None
        self.chamadas = 0
        self.logado = False

    # ---- ciclo de vida
    def __enter__(self):
        if not (self.usuario and self.senha):
            raise RuntimeError("LEGALCLOUD_USER/LEGALCLOUD_PASS ausentes")
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        exe = os.environ.get("PLAYWRIGHT_CHROMIUM_PATH")
        kwargs: dict[str, Any] = {"headless": self.headless}
        if exe:
            kwargs["executable_path"] = exe
        self.browser = self._pw.chromium.launch(**kwargs)
        ctx = self.browser.new_context(locale="pt-BR", timezone_id="America/Sao_Paulo")
        ctx.set_default_timeout(self.timeout)
        self.page = ctx.new_page()
        try:
            self.login()
        except Exception:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *a):
        try:
            if self.browser:
                self.browser.close()
        finally:
            if self._pw:
                self._pw.stop()
        return False

    # ---- localização de campos
    def _localizar(self, nome: str, obrigatorio: bool = True):
        page = self.page
        for cand in self.campos.get(nome, []):
            tipo, _, valor = cand.partition("=")
            try:
                if tipo == "label":
                    loc = page.get_by_label(valor, exact=False)
                elif tipo == "role":
                    papel, _, texto = valor.partition(":")
                    loc = page.get_by_role(papel, name=re.compile(re.escape(texto), re.I))
                elif tipo == "text":
                    padrao = re.compile(valor[1:-1], re.I) if valor.startswith("/") else re.compile(re.escape(valor), re.I)
                    loc = page.get_by_text(padrao)
                elif tipo == "css":
                    loc = page.locator(valor)
                else:
                    continue
                if loc.count() > 0:
                    return loc.first
            except Exception as e:  # noqa: BLE001
                log.debug("candidato %s falhou: %s", cand, e)
        if obrigatorio:
            raise RuntimeError(f"campo '{nome}' não encontrado (candidatos: {self.campos.get(nome)})")
        return None

    def _preencher(self, nome: str, valor: str) -> None:
        loc = self._localizar(nome)
        tag = (loc.evaluate("e => e.tagName") or "").lower()
        if tag == "select":
            opcoes = loc.evaluate("e => Array.from(e.options).map(o => [o.value, o.textContent.trim()])")
            alvo = next((v for v, t in opcoes if valor.lower() == t.lower() or valor.lower() == v.lower()), None) \
                or next((v for v, t in opcoes if valor.lower() in t.lower() or valor.lower() in v.lower()), None)
            if alvo is None:
                raise RuntimeError(f"opção '{valor}' não existe em '{nome}': {[t for _, t in opcoes][:30]}")
            loc.select_option(alvo)
        else:
            loc.click()
            loc.fill("")
            loc.type(valor, delay=20)
            # componentes de autocomplete: confirma com Enter se houver lista aberta
            try:
                loc.press("Tab")
            except Exception:  # noqa: BLE001
                pass

    def _capturar(self, rotulo: str) -> str:
        nome = self.pasta / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{rotulo}.png"
        try:
            self.page.screenshot(path=str(nome), full_page=True)
            return str(nome)
        except Exception:  # noqa: BLE001
            return ""

    # ---- ações
    def login(self) -> None:
        page = self.page
        page.goto(self.cfg.get("url_login") or self.cfg["url"], wait_until="domcontentloaded")
        if self._localizar("senha", obrigatorio=False) is None:
            # sessão já ativa ou página sem login
            self.logado = True
            return
        self._localizar("usuario").fill(self.usuario)
        self._localizar("senha").fill(self.senha)
        self._localizar("entrar").click()
        page.wait_for_load_state("networkidle")
        if self._localizar("senha", obrigatorio=False) is not None:
            cap = self._capturar("login-falhou")
            raise RuntimeError(f"login no Legalcloud não concluído (credenciais recusadas ou layout diferente); captura: {cap}")
        self.logado = True
        log.info("login no Legalcloud ok")

    def abrir_calculadora(self) -> None:
        """Sempre recarrega: após uma simulação o site esconde o formulário ("Simular novo prazo")."""
        url = self.cfg.get("url_calculadora") or self.cfg["url"]
        self.page.goto(url, wait_until="domcontentloaded")
        self.page.wait_for_load_state("networkidle")

    def _sel(self, chave: str):
        css = (self.cfg.get("campos_djen") or {}).get(chave) or CAMPOS_DJEN[chave]
        return self.page.locator(css).first

    def _sel_opcional(self, chave: str):
        """Campo que o site só mostra para alguns tribunais (sistema, instância, suspensões municipais)."""
        loc = self._sel(chave)
        try:
            if loc.count() and loc.is_visible(timeout=1500):
                return loc
        except Exception:  # noqa: BLE001
            pass
        return None

    def conferir(self, tribunal: str, tipo_processo: str, data_publicacao: date, dias: int,
                 dias_corridos: bool, data_local: date | None = None, regime: str | None = None,
                 instancia: str | None = None, fonte: str = "DJEN", **_: Any) -> ResultadoConferencia:
        """`data_publicacao` é a DATA DE DISPONIBILIZAÇÃO (nome mantido por compatibilidade).
        regime: 'Novo CPC' | 'CPP' | 'Juizado Especial' | 'CLT 2017' (default por dias_corridos)."""
        self.chamadas += 1
        page = self.page
        try:
            self.abrir_calculadora()
            regime = regime or ("Juizado Especial" if dias_corridos else "Novo CPC")
            self._sel("meio").select_option(label="DJE" if fonte.upper().startswith("DJE-") or fonte.upper() == "DJE" else "DJEN")
            d = self._sel("data")
            d.click(); d.fill(""); d.type(data_publicacao.strftime("%d/%m/%Y"), delay=25); d.press("Tab")
            self._sel("dias").fill(str(dias))
            self._sel("codigo").select_option(label=regime)
            self._sel("tribunal").select_option(label=sigla_para_site(tribunal))
            self._sel("tipo_processo").select_option(label=self.cfg.get("tipo_processo_padrao", "Eletrônico"))
            page.wait_for_timeout(400)
            sistema = self._sel_opcional("sistema")
            if sistema is not None:
                opcoes = sistema.evaluate("e => Array.from(e.options).map(o => o.textContent.trim())")
                pref = (self.cfg.get("sistema_por_tribunal") or {}).get(sigla_para_site(tribunal)) or self.cfg.get("sistema_padrao", "eSAJ")
                escolha = pref if pref in opcoes else next((o for o in opcoes if not o.lower().startswith("selecionar")), None)
                if escolha:
                    sistema.select_option(label=escolha)
            inst = self._sel_opcional("instancia")
            if inst is not None:
                opcoes = inst.evaluate("e => Array.from(e.options).map(o => o.textContent.trim())")
                alvo = instancia or "1ª Instância"
                inst.select_option(label=alvo if alvo in opcoes else opcoes[0])
            inc = self._sel_opcional("incluir")
            if inc is not None:
                inc.select_option(label=self.cfg.get("suspensoes_municipais", "Não incluir"))
            self._sel("simular").click()
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(1500)
            texto = page.inner_text("body")
            cap = self._capturar(f"conferencia-{sigla_para_site(tribunal)}-{data_publicacao.isoformat()}-{dias}")
            if "selecione um sistema" in texto.lower():
                return ResultadoConferencia(None, None, "site exigiu escolha de sistema (eSAJ/Eproc) não disponível", cap, texto[:500])
            sim = interpretar_simulacao(texto, dias)
            if not sim.get("ok"):
                aviso = mensagem_de_aviso(texto)
                return ResultadoConferencia(None, None, f"site recusou: {aviso}" if aviso else sim.get("motivo", "resultado ilegível"), cap, texto[:4000])
            data_site: date = sim["data_final"]
            obs = []
            if sim["n_contados"] != dias:
                obs.append(f"site contou {sim['n_contados']} dias, esperado {dias}")
            if sim["desconsiderados"]:
                obs.append("site desconsiderou: " + "; ".join(f"{x.strftime('%d/%m')} ({m})" for x, m in sim["desconsiderados"]))
            confere = (data_site == data_local) if data_local else None
            return ResultadoConferencia(data_site, confere, " · ".join(obs), cap, texto[:800])
        except Exception as e:  # noqa: BLE001
            cap = self._capturar("erro")
            return ResultadoConferencia(None, None, f"erro no site: {e}", cap)

    def explorar(self) -> Path:
        """Inventário da calculadora para calibrar `legalcloud.campos`."""
        self.abrir_calculadora()
        page = self.page
        inv = page.evaluate("""() => {
          const lab = e => { const l = e.labels && e.labels[0]; return l ? l.textContent.trim() : (e.getAttribute('aria-label') || e.placeholder || ''); };
          const campos = Array.from(document.querySelectorAll('input,select,textarea,button')).map(e => ({
            tag: e.tagName.toLowerCase(), type: e.type || '', id: e.id || '', name: e.name || '', label: lab(e),
            text: (e.tagName === 'BUTTON' ? e.textContent.trim() : ''), value: e.tagName === 'SELECT' ? '' : (e.value || '').slice(0, 40),
            options: e.tagName === 'SELECT' ? Array.from(e.options).slice(0, 200).map(o => o.textContent.trim()) : undefined }));
          return {url: location.href, title: document.title, campos};
        }""")
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        (self.pasta / f"{stamp}-calculadora.html").write_text(page.content(), encoding="utf-8")
        (self.pasta / f"{stamp}-inventario.json").write_text(json.dumps(inv, ensure_ascii=False, indent=1), encoding="utf-8")
        self._capturar("calculadora")
        return self.pasta / f"{stamp}-inventario.json"


def abrir_conferidor(cfg: dict[str, Any], pasta_logs: Path):
    """Devolve um conferidor (real ou simulado) ou levanta RuntimeError com o motivo da indisponibilidade."""
    mock = os.environ.get("PRAZOS_LEGALCLOUD_MOCK")
    if mock:
        return ConferidorMock(Path(mock))
    if not (os.environ.get("LEGALCLOUD_USER") and os.environ.get("LEGALCLOUD_PASS")):
        raise RuntimeError("LEGALCLOUD_USER/LEGALCLOUD_PASS ausentes no .env")
    try:
        import playwright  # noqa: F401
    except ImportError as e:
        raise RuntimeError("playwright não instalado (pip install playwright && playwright install chromium)") from e
    return ConferidorLegalcloud(cfg, pasta_logs, headless=bool(cfg.get("headless", True)))


def main(argv: list[str] | None = None) -> int:
    import yaml
    from dotenv import load_dotenv
    raiz = Path(__file__).resolve().parents[1]
    load_dotenv(raiz / ".env")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--explorar", action="store_true")
    ap.add_argument("--testar", nargs=3, metavar=("TRIBUNAL", "DATA_PUBLICACAO", "DIAS"))
    ap.add_argument("--corridos", action="store_true")
    ap.add_argument("--visivel", action="store_true", help="abre o navegador visível (fora de servidor)")
    args = ap.parse_args(argv)
    with open(raiz / "config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)["legalcloud"]
    if args.visivel:
        cfg["headless"] = False
    try:
        with abrir_conferidor(cfg, raiz / "logs") as c:
            if args.explorar:
                if not hasattr(c, "explorar"):
                    print("modo simulado não tem o que explorar")
                    return 1
                print("inventário gravado em", c.explorar())
            if args.testar:
                trib, dp, dias = args.testar
                r = c.conferir(trib, "", date.fromisoformat(dp), int(dias), args.corridos, fonte="DJEN")
                print(json.dumps({"data_site": r.data_site.isoformat() if r.data_site else None, "observacao": r.observacao,
                                  "captura": r.captura, "texto": r.texto_resultado}, ensure_ascii=False, indent=1))
    except RuntimeError as e:
        print("LEGALCLOUD INDISPONÍVEL:", e)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
