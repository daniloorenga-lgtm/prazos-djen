# prazos-djen

Rotina matinal de publicações e prazos (Muriel Advogados): coleta no DJEN → classificação pelo modelo →
contagem (CPC) → cartões no Trello → e-mail de relatório. Especificação: `docs/00-especificacao-rotina.md`.
Regras para o modelo: `CLAUDE.md`.

## Instalar

```bash
pip install -r requirements.txt
cp .env.example .env      # preencher TRELLO_KEY/TOKEN, GMAIL_*, (LEGALCLOUD_* só na rotina semanal)
python -m pytest -q       # tudo deve passar antes de qualquer execução real
```

## Preparar (uma vez)

1. **Gmail:** `python scripts/autorizar_gmail.py` (login interativo; cole o refresh token no `.env`).
2. **Trello:** `python src/rodar.py --etapa descobrir-ids` — lê o quadro, cria as etiquetas
   `CONFERIR CONTAGEM` e `DÚVIDA DE CLASSIFICAÇÃO` se faltarem, grava os IDs em `config.yaml`
   (cópia anterior em `config.yaml.bak`; os comentários do YAML se perdem) e lista os membros encontrados.
   **Confira os quatro membros.** Em 17/09/2026 o quadro tinha Pedro, Danilo, Carolina e *Marcela Felix Lira* —
   não há "Marcelo Antonio Muriel" no quadro; ou ele é convidado, ou o quarto membro em `config.yaml` deve ser Marcela.
3. **Calendário:** `dados/feriados-forenses.json` vem com o calendário nacional de 2026 preenchido à mão.
   Substitua pela extração do Legalcloud (rotina semanal, `docs/03-prompt-legalcloud-semanal.md`).
   Enquanto a extração tiver mais de 14 dias (ou nunca tiver ocorrido), todos os prazos saem com `CONFERIR CONTAGEM`.

## Rodar (dia útil, 07:30)

O prompt da routine está em `docs/02-prompt-rotina-diaria.md`. As etapas:

```bash
python src/rodar.py --etapa coletar            # DJEN → estado/pendentes.json
#   (o modelo classifica pendentes.json → estado/classificadas.json, formato em CLAUDE.md)
python src/rodar.py --etapa contar-e-lancar    # conta, busca duplicidade, cria Fatal + Lembrete
python src/rodar.py --etapa autoverificar      # cinco itens do §8
python src/rodar.py --etapa email              # envia (um único e-mail, mesmo sem publicação)
python src/rodar.py --etapa fechar             # só grava estado se o e-mail foi enviado
```

Flags: `--dry-run` (não cria cartão, não envia e-mail; imprime tudo), `--somente-email` (não cria cartão;
envia com aviso "MODO DE VALIDAÇÃO"), `--data AAAA-MM-DD [--ate AAAA-MM-DD]` (força a janela),
`--etapa email --erro-coleta "motivo"` (e-mail de alerta quando a coleta falhou duas vezes).

Sem `--etapa`, roda tudo e **para** depois da coleta se `estado/classificadas.json` não existir.

### Validar sem rede
`PRAZOS_DJEN_MOCK=tests/fixtures/djen-mock.json python src/rodar.py --etapa coletar --dry-run --data 2026-09-16 --ate 2026-09-17`
usa uma fonte simulada no formato da API.

## Reprocessar um dia

```bash
python src/rodar.py --etapa coletar --data 2026-09-16      # janela = só esse dia
# classificar → contar-e-lancar → autoverificar → email → fechar
```
Publicações já registradas em `estado/processados.json` (60 dias) não são relançadas; o e-mail informa
quantas foram puladas. Para forçar relançamento, remova o hash de `processados.json` (nunca apague o arquivo inteiro:
ele é a proteção contra cartões duplicados). A busca de duplicidade no quadro (por número CNJ + ato) é a segunda proteção.

## Atualizar o calendário

Rotina semanal local (Claude Desktop + Chrome, `docs/03-prompt-legalcloud-semanal.md`). Antes de gravar:
`python src/validar_calendario.py dados/feriados-forenses.json`. Se a extração falhar, grave
`dados/feriados-forenses.status.json` com `{"sucesso": false, ...}` — a rotina diária avisa no e-mail.
Edições manuais são aceitas (mesmo formato).

## Se o e-mail vier com "NÃO LANÇADOS NO TRELLO"

1. O bloco traz título, prazo fatal, lembrete, processo, etiquetas e o motivo da falha. **Lance os dois cartões à mão**
   na lista Prazos (modelo em `CLAUDE.md`, descrição completa em `estado/lancados.json` → `cartao_fatal.descricao`).
2. Veja o motivo em `logs/execucao-*.log` (Trello fora, token expirado, ID de lista/etiqueta errado).
3. Não rode `contar-e-lancar` de novo sem antes verificar o quadro: se o cartão fatal foi criado e só o lembrete falhou,
   o e-mail diz isso ("cartão fatal criado ... mas o LEMBRETE falhou") — crie só o lembrete.
4. Corrigida a causa, é seguro reexecutar `contar-e-lancar`: a busca de duplicidade impede recriar o que já existe.

## Limites conhecidos

- O Swagger oficial do DJEN não pôde ser lido no ambiente de construção (rede bloqueada). Parâmetros e campos
  vieram de implementações públicas (ver cabeçalho de `src/coletar.py`). Confira `logs/djen-*.json` na primeira coleta real.
- A conferência de prazos no site do Legalcloud (§0 `uso_2`) não é feita pela rotina diária remota (exige navegador):
  os cartões `CONFERIR CONTAGEM` saem com "Legalcloud: pendente de conferência" e o rodapé informa 0 conferidos.
- O módulo de e-mail chama-se `src/relatorio_email.py` (não `email.py`) para não sombrear o pacote `email` da biblioteca padrão.
- `dias_corridos` (Juizados/penal) não aplica o recesso — sempre sai com `CONFERIR CONTAGEM`.
