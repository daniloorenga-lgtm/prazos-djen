#!/usr/bin/env bash
# Primeiro teste real na sua máquina: publicações de hoje, sem cartões, e-mail só para você.
# Uso: ./scripts/testar_hoje.sh            (para na classificação; depois: ./scripts/testar_hoje.sh --continuar)
set -euo pipefail
cd "$(dirname "$0")/.."
HOJE="$(date +%F)"
if [ "${1:-}" != "--continuar" ]; then
  [ -f .env ] || { echo "crie o .env a partir de .env.example"; exit 1; }
  python -m pytest -q
  python src/legalcloud.py --testar TJSP 2026-09-17 15 || echo "Legalcloud: calibrar com 'python src/legalcloud.py --explorar' (a rotina segue sem conferência)"
  python src/rodar.py --etapa coletar --somente-email --data "$HOJE" || python src/rodar.py --etapa coletar --somente-email --data "$HOJE" || {
    python src/rodar.py --etapa email --para dorenga@muriel.adv.br --erro-coleta "coleta falhou duas vezes"; exit 1; }
  echo
  echo ">>> Agora classifique estado/pendentes.json em estado/classificadas.json (formato em CLAUDE.md)"
  echo ">>> e rode: ./scripts/testar_hoje.sh --continuar"
  exit 0
fi
python src/rodar.py --etapa contar-e-lancar --somente-email
python src/rodar.py --etapa autoverificar || true
python src/rodar.py --etapa email --para dorenga@muriel.adv.br
echo ">>> Teste concluído. Não rode 'fechar' num teste."
