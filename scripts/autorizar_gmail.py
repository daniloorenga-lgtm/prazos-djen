"""Gera o GMAIL_REFRESH_TOKEN uma única vez, interativamente.

Pré-requisitos (feitos por você no Google Cloud Console):
  1. Projeto com a API Gmail ativada.
  2. Tela de consentimento OAuth (tipo "Externo" ou "Interno"; se Externo, adicione dorenga@muriel.adv.br
     como usuário de teste ou publique o app — em modo "teste" o refresh token expira em 7 dias).
  3. Credencial OAuth "Aplicativo para computador" → copie client id e secret para o .env
     (GMAIL_CLIENT_ID / GMAIL_CLIENT_SECRET).

Uso:  python scripts/autorizar_gmail.py
      Abre o navegador (ou imprime a URL). Faça login com dorenga@muriel.adv.br e autorize.
      O script imprime o refresh token: cole-o em GMAIL_REFRESH_TOKEN no .env. Nunca o versione.
Escopo: gmail.send (só envio).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

try:
    from google_auth_oauthlib.flow import InstalledAppFlow
except ImportError:
    print("instale as dependências: pip install -r requirements.txt")
    sys.exit(1)

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]


def main() -> int:
    cid, csec = os.environ.get("GMAIL_CLIENT_ID"), os.environ.get("GMAIL_CLIENT_SECRET")
    if not (cid and csec):
        print("Preencha GMAIL_CLIENT_ID e GMAIL_CLIENT_SECRET no .env antes de rodar.")
        return 1
    cfg = {"installed": {"client_id": cid, "client_secret": csec,
                         "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                         "token_uri": "https://oauth2.googleapis.com/token",
                         "redirect_uris": ["http://localhost"]}}
    flow = InstalledAppFlow.from_client_config(cfg, SCOPES)
    sem_navegador = "--sem-navegador" in sys.argv
    creds = flow.run_local_server(port=0, open_browser=not sem_navegador, prompt="consent",
                                  access_type="offline")
    if not creds.refresh_token:
        print("Google não devolveu refresh token. Revogue o acesso em myaccount.google.com/permissions e rode de novo.")
        return 1
    print("\nGMAIL_REFRESH_TOKEN obtido. Cole no .env (não mostre a ninguém):\n")
    print(f"GMAIL_REFRESH_TOKEN={creds.refresh_token}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
