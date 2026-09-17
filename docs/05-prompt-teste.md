# Prompt do primeiro teste (cole no Claude Code — Desktop na pasta do projeto, ou sessão web após docs/04)

Leia CLAUDE.md e README.md. Execute o primeiro teste real da rotina, para hoje, sem criar cartões e com e-mail só para mim:

1. `python -m pytest -q` — tudo deve passar.
2. `python src/legalcloud.py --testar TJSP 2026-09-17 15` (data = disponibilização; esperado 09/10/2026). Se falhar por campo não encontrado, rode `python src/legalcloud.py --explorar`, leia o inventário em `logs/legalcloud/` e ajuste `legalcloud.campos` no `config.yaml` até o `--testar` responder; se o site esperar a data de publicação (resultado um dia útil a menos), mude `legalcloud.data_informada` para `publicacao`.
3. `python src/rodar.py --etapa coletar --somente-email --data $(date +%F)`. Se falhar, repita uma vez; se falhar de novo, vá ao passo 7 com `--erro-coleta`.
4. Classifique cada publicação de `estado/pendentes.json` conforme o CLAUDE.md e grave `estado/classificadas.json`. Não calcule datas.
5. `python src/rodar.py --etapa contar-e-lancar --somente-email` (conta e confere no Legalcloud; nenhum cartão).
6. `python src/rodar.py --etapa autoverificar`.
7. `python src/rodar.py --etapa email --para dorenga@muriel.adv.br`.
8. NÃO rode `--etapa fechar`.
Mostre a saída completa de cada etapa e termine com: N publicações, M prazos, K alertas, conferidos no Legalcloud, divergências, e-mail enviado sim/não.
