# PROMPT SEMANAL — calendário forense via Legalcloud

> Agendar como tarefa **local** no Claude Code Desktop (segunda-feira, 07:00, com Claude in Chrome habilitado), na pasta do projeto `prazos-djen`. Roda só com o computador ligado — por isso é separada da rotina diária, que nunca depende dela em tempo real.

---

Você vai atualizar `dados/feriados-forenses.json`, o calendário que a rotina diária usa para contar prazos. Fonte: a calculadora de prazos do Legalcloud (https://app.legalcloud.com.br/calculadora/).

## Acesso
- Abra o site no Chrome. Use a sessão já autenticada do navegador. **Se não houver sessão ativa, pare e me avise** — não digite credenciais; eu faço o login e você continua na próxima execução.

## O que extrair
Para cada tribunal listado em `config.yaml` → `tribunais_monitorados` (TJSP, TJPR, TRF3, STJ, STF e os demais que constarem), obtenha do site, para os próximos 120 dias:
1. Feriados nacionais, estaduais e forenses reconhecidos pelo tribunal.
2. Feriados municipais das comarcas listadas em `config.yaml` → `comarcas` (a rotina acrescenta comarcas conforme aparecem processos; se alguma não estiver disponível no site, registre em `nao_encontradas`).
3. Suspensões de prazo e pontos facultativos publicados pelo tribunal.
4. Datas do recesso (20/12–20/01) conforme o site as trate.

Se o site não separar as categorias, registre tudo como `feriado` com o campo `fonte_texto` contendo a descrição literal.

## Formato de saída
```json
{
  "extraido_em": "2026-09-21T07:04:00-03:00",
  "fonte": "Legalcloud",
  "horizonte_ate": "2027-01-19",
  "tribunais": {
    "TJSP": {
      "feriados": [{"data": "2026-10-12", "descricao": "Nossa Senhora Aparecida", "fonte_texto": "..."}],
      "suspensoes": [{"inicio": "2026-12-20", "fim": "2027-01-20", "descricao": "Recesso"}],
      "comarcas": {"Águas de Lindóia": [{"data": "...", "descricao": "..."}]}
    }
  },
  "nao_encontradas": []
}
```

## Regras
- **Mescle, não substitua:** carregue o arquivo atual, adicione o que é novo, mantenha o que já existe para datas passadas (a rotina reprocessa dias antigos às vezes). Só remova uma data se o site a tiver removido explicitamente — e, nesse caso, registre a remoção em `alteracoes` com data e motivo.
- Antes de gravar, execute `python src/validar_calendario.py dados/feriados-forenses.json` (o script verifica JSON, datas válidas, ausência de duplicatas e que os feriados nacionais fixos estão presentes). Se reprovar, corrija; se não conseguir, não grave e avise.
- Faça commit do arquivo com mensagem `calendário: atualização semanal AAAA-MM-DD`.
- Se o site estiver indisponível ou mudar de layout a ponto de você não conseguir extrair com confiança, **não altere o arquivo**. Grave `dados/feriados-forenses.status.json` com `{"ultima_tentativa": ..., "sucesso": false, "motivo": ...}` — a rotina diária lê esse arquivo e avisa no e-mail quando a última atualização bem-sucedida tem mais de 14 dias.

## Encerramento
Resuma em três linhas: quantos feriados/suspensões novos, quais comarcas não encontradas, e a data do horizonte coberto.
