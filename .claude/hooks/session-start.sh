#!/bin/bash
# Hook de inicialização para o Claude Code na web: instala dependências para testes e execução.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR"
python3 -m pip install -q -r requirements.txt

# Chromium: o ambiente web já traz um em /opt/pw-browsers; aponte o driver para ele em vez de baixar outro.
CHROME="$(ls -d /opt/pw-browsers/chromium-*/chrome-linux/chrome 2>/dev/null | sort | tail -1 || true)"
if [ -n "$CHROME" ]; then
  echo "export PLAYWRIGHT_CHROMIUM_PATH=\"$CHROME\"" >> "$CLAUDE_ENV_FILE"
else
  python3 -m playwright install chromium || echo "aviso: chromium não instalado; conferência no Legalcloud ficará indisponível"
fi
echo 'export PYTHONPATH="$CLAUDE_PROJECT_DIR/src"' >> "$CLAUDE_ENV_FILE"
echo "session-start: dependências prontas"
