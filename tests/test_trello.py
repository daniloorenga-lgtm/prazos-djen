"""Cliente Trello com sessão HTTP falsa: sem rede, sem credenciais reais."""
import json

import pytest

from modelos import Cartao
from trello import ClienteTrello, ErroTrello, vencimento_utc


class Resp:
    def __init__(self, status, dados):
        self.status_code = status
        self._dados = dados
        self.text = json.dumps(dados)

    def json(self):
        return self._dados


class SessaoFalsa:
    def __init__(self):
        self.chamadas = []
        self.cartoes = []
        self.n = 0

    def get(self, url, params=None, timeout=None):
        self.chamadas.append(("GET", url, params))
        if url.endswith("/cards/all"):
            return Resp(200, self.cartoes)
        if url.endswith("/lists"):
            return Resp(200, [{"id": "L1", "name": "Prazos"}, {"id": "L2", "name": "Providências"}])
        if url.endswith("/labels"):
            return Resp(200, [{"id": "E1", "name": "Prazo Fatal"}, {"id": "E2", "name": "Lembrete"}])
        if url.endswith("/members"):
            return Resp(200, [{"id": "M1", "fullName": "Danilo Orenga"}, {"id": "M2", "fullName": "Pedro Augusto Di Giovanni Boro"},
                              {"id": "M3", "fullName": "Carolina Naves"}, {"id": "M4", "fullName": "Marcela Felix Lira"}])
        return Resp(200, {"id": "B", "name": "Quadro", "url": "https://trello.com/b/x"})

    def request(self, metodo, url, params=None, json=None, timeout=None):
        self.chamadas.append((metodo, url, json))
        if metodo == "DELETE":
            raise AssertionError("delete nunca deve ser chamado")
        if url.endswith("/cards"):
            self.n += 1
            c = {"id": f"C{self.n}", "shortUrl": f"https://trello.com/c/C{self.n}", **json}
            self.cartoes.append({"id": c["id"], "name": json["name"], "desc": json["desc"], "shortUrl": c["shortUrl"], "due": json["due"]})
            return Resp(200, c)
        if url.endswith("/labels"):
            return Resp(200, {"id": "E-nova", "name": json["name"]})
        return Resp(200, {})


@pytest.fixture
def cli():
    s = SessaoFalsa()
    c = ClienteTrello(key="k", token="t", board_id="B", sessao=s, pausa=0)
    return c, s


def test_vencimento_23h59_sao_paulo_em_utc():
    assert vencimento_utc("2026-10-09") == "2026-10-10T02:59:00.000Z"


def test_sem_credenciais_falha_cedo(monkeypatch):
    for v in ("TRELLO_KEY", "TRELLO_TOKEN", "TRELLO_BOARD_ID"):
        monkeypatch.delenv(v, raising=False)
    with pytest.raises(ErroTrello):
        ClienteTrello()


def test_criar_par_cria_fatal_depois_lembrete_com_link_cruzado(cli):
    c, s = cli
    fatal = Cartao("fatal", "A X B | Apelação", "desc", "2026-10-09", ["Prazo Fatal", "CONFERIR CONTAGEM"], [])
    lemb = Cartao("lembrete", "A X B | Apelação", "desc", "2026-10-02", ["Lembrete"], [])
    f, l = c.criar_par(fatal, lemb, "L1", ["M1", "M2"], {"Prazo Fatal": "E1", "Lembrete": "E2", "CONFERIR CONTAGEM": None}, "America/Sao_Paulo", "23:59")
    posts = [ch for ch in s.chamadas if ch[0] == "POST"]
    assert posts[0][2]["name"] == "A X B | Apelação" and posts[0][2]["idLabels"] == "E1"   # etiqueta sem ID é omitida
    assert posts[0][2]["due"] == "2026-10-10T02:59:00.000Z" and posts[0][2]["idMembers"] == "M1,M2"
    assert posts[1][2]["idLabels"] == "E2" and posts[1][2]["due"] == "2026-10-03T02:59:00.000Z"
    assert f.url == "https://trello.com/c/C1" and l.url == "https://trello.com/c/C2"
    puts = [ch for ch in s.chamadas if ch[0] == "PUT"]
    assert "Cartão vinculado: https://trello.com/c/C2" in puts[0][2]["desc"]
    assert "Cartão vinculado: https://trello.com/c/C1" in puts[1][2]["desc"]


def test_busca_por_processo_e_mesmo_ato(cli):
    c, s = cli
    s.cartoes = [{"id": "X", "name": "A X B | Recurso de Apelação", "desc": "... 0008819-53.2019.8.26.0100 ...", "closed": True},
                 {"id": "Y", "name": "A X B | Embargos de Declaração", "desc": "... 0008819-53.2019.8.26.0100 ...", "closed": False}]
    assert len(c.buscar_por_processo("0008819-53.2019.8.26.0100")) == 2      # inclui arquivado
    assert c.existente_para_ato("0008819-53.2019.8.26.0100", "C X D | recurso de apelacao")["id"] == "X"
    assert c.existente_para_ato("0008819-53.2019.8.26.0100", "A X B | Contrarrazões") is None
    assert c.buscar_por_processo("") == []


def test_descobrir_ids_cria_so_as_duas_etiquetas_e_resolve_membros(cli):
    c, s = cli
    config = {"trello": {"lista_destino": "Prazos", "membros": [
        {"nome": "Danilo", "nome_trello": "Danilo Orenga"}, {"nome": "Pedro"}, {"nome": "Carolina"}, {"nome": "Marcelo"}]}}
    r = c.descobrir_ids(config)
    assert r["lista_destino_id"] == "L1"
    assert r["etiquetas"]["Prazo Fatal"] == "E1" and r["etiquetas"]["CONFERIR CONTAGEM"] == "E-nova"
    criadas = [ch[2]["name"] for ch in s.chamadas if ch[0] == "POST" and ch[1].endswith("/labels")]
    assert criadas == ["CONFERIR CONTAGEM", "DÚVIDA DE CLASSIFICAÇÃO"]
    ids = {m["nome"]: m["id"] for m in r["membros"]}
    assert ids == {"Danilo": "M1", "Pedro": "M2", "Carolina": "M3", "Marcelo": None}
    assert r["membros_nao_encontrados"] == ["Marcelo"]


def test_metodo_proibido(cli):
    c, _ = cli
    with pytest.raises(ErroTrello):
        c._req("DELETE", "/cards/1")


def test_existente_prefere_fatal_e_acha_lembrete(cli):
    """Regressão 18/09/2026: o Lembrete (mesmo título) era tomado como 'existente' e tinha o vencimento sobrescrito."""
    c, s = cli
    cnj = "4123639-79.2026.8.26.0000"
    s.cartoes = [{"id": "L", "name": "S X N | Contrarrazões de Agravo", "desc": f"... {cnj} ...", "closed": False, "idLabels": ["LEMB"]},
                 {"id": "F", "name": "S X N | Contrarrazões de Agravo", "desc": f"... {cnj} ...", "closed": False, "idLabels": ["FATAL"]}]
    assert c.existente_para_ato(cnj, "S X N | contrarrazoes de agravo", id_lembrete="LEMB")["id"] == "F"
    assert c.lembrete_para_ato(cnj, "S X N | contrarrazoes de agravo", id_lembrete="LEMB")["id"] == "L"
    # sem o id da etiqueta (config antigo) mantém o comportamento anterior: primeiro que aparecer
    assert c.existente_para_ato(cnj, "S X N | contrarrazoes de agravo")["id"] == "L"
    # só existe o lembrete: ele é devolvido para não criar par duplicado
    s.cartoes = s.cartoes[:1]
    assert c.existente_para_ato(cnj, "S X N | contrarrazoes de agravo", id_lembrete="LEMB")["id"] == "L"
