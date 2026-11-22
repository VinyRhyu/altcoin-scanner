# Auditoria de agendamento, coleta e cobertura do scanner (somente pesquisa)

**Data:** 22/11/2026 UTC. **Branch:** `test/systematic-trading-research`.  
**PR draft:** [#3](https://github.com/VinyRhyu/altcoin-scanner/pull/3).  
**Estado:** nenhuma ordem, nenhuma alteração na `master` ou `market-data`, nenhum merge, nenhum dado financeiro pessoal neste relatório.

## Pergunta investigada

O scanner deixa de detectar oportunidades porque os cálculos são lentos, porque o agendamento não inicia coleta a cada fechamento de candle ou porque o ativo não integra o universo observado?

As três hipóteses foram avaliadas separadamente. A **latência de coleta e decisão** foi medida num dry-run iniciado por push, a **cadência de agendamento** foi conferida nos registros públicos do workflow de produção, e a **cobertura de símbolo** foi confrontada com a lista curada e a API pública de instrumentos da OKX.

### 1. Duração real de coleta e decisão — execução experimental completa

Fonte: [Actions #45043514392](https://github.com/VinyRhyu/altcoin-scanner/actions/runs/45043514392), dois jobs concluídos com sucesso. O módulo `research/pipeline_latency_probe.py` instrumentou o runner com relógio monotônico, pontos de tempo UTC e eventos **opt-in** dentro de `collector.collect`. O código só registra marcações quando a variável `RESEARCH_TIMING_LOG` está definida, portanto coleta e demais filtros mantêm comportamento padrão no uso normal.

| Etapa | Duração observada |
|---|---:|
| Descoberta de tickers e metadados | **0,375 s** |
| Candidatos e candles do pré-filtro 15m | **11,155 s** |
| Candles dos timeframes adicionais | **19,033 s** |
| **Coleta inteira** (incluindo overhead) | **31,132 s** |
| **Cálculo dos planos condicionais** | **0,127 s** |
| Primeiro checkpoint no runner até preparar artifact | **31,425 s** |
| Universo realmente capturado | **47 ativos** |
| Idade mediana da cotação OKX no início do cálculo dos sinais | **31,384 s** |
| Percentil 90 da idade dessas cotações | **31,475 s** |

O payload `collected_utc` é definido **antes** da chamada longa de coleta, portanto é timestamp de *início da captura*, não de conclusão nem publicação. O dry-run começou a coletar **485,318 s após o último fechamento 15m** naquele relógio, mas **não estava agendado pelo cron de produção** e portanto esta diferença não deve ser interpretada como fila do GitHub.

Foi utilizado o coletor real em modo read-only: **sem publicação em `market-data`**, sem sinais em produção, sem ordens. As métricas de idade das cotações são comparações entre timestamps de tickers públicos da OKX e o relógio do runner, não prova de preço executável ou preenchimento de ordem.

### 2. Cadência do cron principal — hipótese mais urgente

No arquivo [`.github/workflows/scan.yml`](https://github.com/VinyRhyu/altcoin-scanner/blob/master/.github/workflows/scan.yml), o agendamento está configurado para **minutos 01, 16, 31 e 46 UTC** de cada hora, ou seja, um alvo de **4 execuções/hora**, 60 segundos depois dos fechamentos dos candles de 15m.

Um job independente de leitura pública da API de Actions executou `research/scheduled_workflow_health.py`. Janela de auditoria **fixada antes de consultar** os resultados: **22/11/2026, 00:00–16:00 UTC, extremo superior exclusivo**.

| Métrica | Registro |
|---|---:|
| Horários nominais de cron nesta janela de 16h | **64** |
| Registros de runs criados com `event=schedule` do scanner no intervalo | **2** |
| Relação quantidade registrada / quantidade nominal | **2/64 = 3,125%** |
| `created_at` do primeiro run | **03:29:38 UTC** |
| `created_at` do segundo run | **11:53:29 UTC** |
| Intervalo entre essas duas criações | **8h23min51s** |

Fonte auditável: [Actions #45043955857](https://github.com/VinyRhyu/altcoin-scanner/actions/runs/45043955857), job `Compare observed master cron runs to expected 15m cadence` com conclusão **success**. O artifact `master-schedule-cadence-readonly` inclui os resultados, sem detalhes privados.

**Interpretação:** há uma **discrepância grande de frequência** entre o cron configurado e o número de execuções agendadas registradas. Os dados não permitem identificar quais horários foram descartados nem provar que o problema foi a fila do GitHub: `created_at` pode não corresponder ao horário planejado, alguns runs podem atravessar a borda da janela e a definição atual de cron pode ter mudado durante o período. A documentação oficial do GitHub reconhece que eventos agendados podem atrasar ou ser descartados sob carga: [GitHub Docs — troubleshooting scheduled events](https://docs.github.com/en/actions/how-tos/troubleshoot-workflows).

**Verificação adicional contra produção:** o run [#45019516353](https://github.com/VinyRhyu/altcoin-scanner/actions/runs/45019516353) foi criado às **11:53:29 UTC**; o log começou por volta de **11:53:32**, a coleta começou aproximadamente às **11:53:33**, terminou às **11:54:13** e a publicação no repositório foi registrada aproximadamente às **11:54:34 UTC**. A parte de coleta foi `40 segundos: nesta amostra, o atraso predominou **antes do primeiro passo do job**, embora ainda não possamos classificar a origem do atraso do agendamento (disparo, entrega do evento, fila).

Essa observação é uma prioridade **operacional**, independente de encontrar uma estratégia lucrativa.

### 3. Integridade do universo em coletas subsequentes

Dois dry-runs subsequentes de coleta na mesma branch [#45043758433](https://github.com/VinyRhyu/altcoin-scanner/actions/runs/45043758433) e [#45043955857](https://github.com/VinyRhyu/altcoin-scanner/actions/runs/45043955857) foram corretamente **reprovados** por retornarem respectivamente **9 e 8 tickers elegíveis**, abaixo de `MIN_VALIDATED=15`.

**Não** foi reduzido esse limite para obter CI verde; sem 15 ativos elegíveis a coleta **não deve ser tratada como completa**. Não foi estabelecido se a redução momentânea se deveu a latência de ticker, restrições de liquidez/spread, flutuação do mercado ou problema transitório na resposta pública da corretora. É um problema de confiabilidade separado que merece contagem de exclusões **por motivo** em uma investigação seguinte.

### 4. Cobertura de ativos — verificação pública de um símbolo

Como teste de um símbolo público (**ORCA**, sem dados de posição ou retorno), verificamos duas camadas independentes:

- A base **ORCA não pertence a `collector.CRYPTO_BASES`**, lista curada obrigatória mesmo quando se consulta a seleção top100. O símbolo não chega à etapa de padrões LONG/SHORT na estratégia atual, por motivo de cobertura.
- Consulta independente ao endpoint público `GET /api/v5/public/instruments` na OKX com `instType=SWAP&instId=ORCA-USDT-SWAP` retornou **`code="51001"`**, isto é, **identificador de instrumento não encontrado** no momento da consulta (22/11/2026 UTC, aproximadamente 16h29). O módulo `research/asset_coverage_probe.py` agora distingue esse retorno de falhas transitórias da API. Isso **não determina** se há contrato em outra corretora nem prova o estado histórico do instrumento.

Fonte: [Actions #45044104730](https://github.com/VinyRhyu/altcoin-scanner/actions/runs/45044104730), job `Check public asset coverage independently of collector success`, com sucesso. Nenhum horário, alavancagem, resultado ou movimentação de conta de terceiros foi salvo no repositório.

### Testes e implementação

- `research/pipeline_latency_probe.py`: relógio de parede + monotônico para estágios, idade de tickers na leitura, comparação com fechamento 15m, relatório sem ordens e sem alegar medir fila/delivery.
- `collector.py`: hooks de tempo **opt-in**, inertes se `RESEARCH_TIMING_LOG` não estiver definida; nenhum filtro ou plano modificado.
- `research/scheduled_workflow_health.py`: recebe metadados públicos de execuções GitHub e verifica uma janela fixa versus cron nominal, com cautela sobre atraso/perda de eventos.
- `research/asset_coverage_probe.py`: diferencia `allowlist`, existência de contrato atual da OKX e seleção de universo, sem dados pessoais ou sinal de trade.
- Testes em `tests/test_pipeline_latency_probe.py`, `tests/test_scheduled_workflow_health.py` e `tests/test_asset_coverage_probe.py`: origem de relógios, no-orders, JSON, isolamento de cron, deslistagem atual versus informação histórica, e ausência de dados financeiros de usuário.
- `.github/workflows/research-pipeline-timing.yml`: jobs independentes para testes, teste de velocidade da coleta, cadência de cron e cobertura pública, permitindo concluir os diagnósticos independentes mesmo quando a coleta falha.

## Decisão e próximos passos

1. **Não otimizar momentum, stop, ATR nem as entradas agora**: o obstáculo de cobertura e cadência é anterior ao cálculo da estratégia.
2. Criar uma opção de **agendamento confiável e observabilidade real** fora da dependência exclusiva do `schedule` de GitHub Actions, com monitor de batimentos (heartbeat) a cada fechamento 15m. Primeiro validar na branch experimental com métricas de entrega, nunca mudar produção sem comparação.
3. Medir, com eventos separados, despacho nominal, criação do run, início do runner, início/fim das APIs, fim da coleta, fim do cálculo e fim da publicação. A primeira medição mostra `31s de coleta, mas isso **não** informa se a etapa de publicação/alerta chega no horário certo.
4. Investigar, sem afrouxar filtros, porque algumas tentativas retornaram **8–9 tickers elegíveis** depois de outra ter retornado 47. Exigir motivo de exclusão por filtro e origem do dado.
5. Para símbolos externos, conferir **local de negociação, contrato e elegibilidade da própria corretora** antes de comparar históricos. Não misturar observações externas com a expectativa financeira demonstrada do scanner.
6. Prosseguir com coleta prospectiva e paper research. **Nenhuma operação real do usuário foi registrada, publicada ou usada como dado de treino.**

**Segurança:** sem ordens, sem merge, nenhuma modificação na `master` ou `market-data`, PR #3 permanece draft.
