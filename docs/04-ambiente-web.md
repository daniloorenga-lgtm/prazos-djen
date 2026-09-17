# Rodar a rotina numa sessão do Claude Code na web (opção 1)

> **Limitação constatada em 17/09/2026:** com a rede liberada, Trello e Legalcloud funcionam a partir da sessão web, mas a API
> do DJEN responde `403 — The Amazon CloudFront distribution is configured to block access from your country`. O ambiente web
> sai por IP fora do Brasil. Enquanto isso valer, a **coleta** precisa rodar de um computador no Brasil; a conferência no
> Legalcloud e o Trello podem continuar na web.

O ambiente web só alcança os domínios liberados na política de rede. Para a rotina funcionar a partir dele:

## 1. Política de rede do ambiente
Em claude.ai/code → Environments → ambiente **Casa** (ou crie um "prazos-djen") → Network access:
escolha a política que permita domínios específicos e inclua:

- `comunicaapi.pje.jus.br` (DJEN — obrigatório)
- `api.trello.com` (cartões)
- `app.legalcloud.com.br` (conferência de prazos)
- `oauth2.googleapis.com` e `gmail.googleapis.com` (envio pela Gmail API)
- `pypi.org` e `files.pythonhosted.org` (já liberados por padrão)

Documentação: https://code.claude.com/docs/en/claude-code-on-the-web

## 2. Variáveis de ambiente (no mesmo ambiente)
As mesmas do `.env.example`, com valores: `TRELLO_KEY`, `TRELLO_TOKEN`, `TRELLO_BOARD_ID=683a306e577239c1d30e30ad`,
`GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN`, `LEGALCLOUD_USER`, `LEGALCLOUD_PASS`.
O `GMAIL_REFRESH_TOKEN` só pode ser gerado uma vez, localmente, com `python scripts/autorizar_gmail.py`
(precisa de navegador para o login Google).

## 3. Hook de inicialização
`.claude/hooks/session-start.sh` (registrado em `.claude/settings.json`) instala as dependências e aponta o
Playwright para o Chromium pré-instalado. Vale para toda sessão nova a partir do branch onde estiver.

## 4. O que pedir na sessão
Cole o conteúdo de `docs/05-prompt-teste.md`. Para a rotina diária definitiva, agende uma Routine com
`docs/02-prompt-rotina-diaria.md` (dias úteis, 07:30 America/Sao_Paulo = 10:30 UTC).
