# Correção experimental do relógio do ticker — comparação shadow com o mesmo payload

**Data UTC:** 22/11/2026. **Branch:** `test/systematic-trading-research`.  
**PR:** [#3, draft](https://github.com/VinyRhyu/altcoin-scanner/pull/3).  
**Validações concluídas:** [Execução integral de proveniência #45051914172](https://github.com/VinyRhyu/altcoin-scanner/actions/runs/45051914172) (**2 jobs success; 271/271 testes**) e [Shadow com o mesmo payload #45051903271](https://github.com/VinyRhyu/altcoin-scanner/actions/runs/45051903271) (**2 jobs success; 271/271 testes**).

## Contexto

O coletor original captura `now_ms` **antes** da chamada à lista permitida e das consultas de instrumentos/tickers. Ao receber a resposta, o filtro de timestamp testa `0 <= now_ms - ticker_timestamp <= 300000`. Cotações obtidas após o início da coleta podem ter horário posterior a `now_ms`, mesmo estando válidas **no momento da resposta**.

Isso foi reproduzido [no teste anterior #45050823381](https://github.com/VinyRhyu/altcoin-scanner/actions/runs/45050823381): num dos três samples da API pública, **69 tickers** foram rejeitados por timestamp posterior ao relógio inicial, nenhum por idade superior a cinco minutos. Reprocessar o **mesmo payload** com o horário de recebimento mudou os ativos elegíveis de **12 para 47**. Fatores como volume, spread, tick, padrões e custo permaneceram idênticos. A falha é intermitente, não necessariamente explica toda coleta anterior com 8/9 ativos elegíveis.

## Implementação

### Modo original — inalterado por padrão

A chamada `collector.tickers(allowed,now_ms)` mantém o modo `legacy`. A chamada `collector.collect(now_ms)` também mantém o modo legado quando não recebe explicitamente `research_clock_mode="receipt"`. O workflow `.github/workflows/scan.yml` de produção **não** define a variável `RESEARCH_TICKER_CLOCK_MODE`, portanto **não adota o experimento**.

Os limites permanecem: volume mínimo US$2 milhões, spread máximo 0,25% na descoberta, validade do ticker **300s**, mínimo de **15** ativos elegíveis e sequência original dos filtros; stops, R:R, alavancagem e decisão não foram alterados.

### Modo receipt — somente pesquisa

No dry-run experimental, o coletor usa a flag **`RESEARCH_TICKER_CLOCK_MODE=receipt`**.

1. Registra quando o scan foi iniciado, quando a requisição de ticker começou e quando a **resposta de ticker chegou**.
2. Faz **uma única requisição pública** para a lista de instrumentos e uma para tickers. A mesma lista de tickers é submetida ao filtro original (`scan_started`) e ao filtro experimental (`ticker_fetch_completed`). O diagnóstico registra símbolos adicionados/removidos e contagem nas duas políticas, sem API duplicada para escolher um resultado favorável.
3. Compara o timestamp da exchange **contra o instante de recebimento**; rejeita se timestamp estiver no futuro do recebimento ou se tiver idade acima de **cinco minutos**. Não aceita dados futuros só porque passaram no modo novo.
4. Ainda valida todos os candles 15m, 1h e 2h contra `scan_started`. Nunca promove candles que só fecharam **depois** do scan ser iniciado; continua rejeitando candles desatualizados.
5. Antes de marcar snapshot como pronto, valida novamente se os tickers selecionados ainda têm idade de até 300s e se os candles continuam fechados e atuais.
6. O campo `collected_utc` no **modo experimental apenas** é definido no momento **posterior à coleta completa**; a proveniência inclui `scan_started_utc`, `ticker_fetch_started_utc`, `ticker_fetch_completed_utc` e `decision_data_ready_utc`. Assim a análise não atribui um ticker capturado mais tarde ao instante antigo do início da coleta.
7. Permite calcular LONG/SHORT na mesma fonte, sempre `order_allowed=false` e `signals_allowed=false`. A comparação shadow usa subconjunto de ativos que também teriam passado na seleção inicial original, sem inventar candles/fills para ativos que não chegaram à validação.

**Limite importante:** a versão `legacy` em shadow é um **subconjunto do universo OHLCV completamente validado sob receipt**, não uma reconstrução integral e independente de uma segunda execução legada. Isso é explicitamente indicado no relatório. A evidência de 12→47 está no filtro de **tickers**, não comprova que o mesmo acréscimo se converta em padrões operacionais.

## Teste real após a implementação

### Execução #45051914172 — read-only, relógios sincronizados

O coletor experimental completou **47 ativos OKX** com candles dos três timeframes. O relatório de auditoria confirmou `scan_start <= ticker_fetch_start <= ticker_fetch_complete <= data_ready <= decision`, ticker máximo 300s, ausência de candles futuros e nenhuma ordem. O filtro original e o experimental obtiveram **47 tickers elegíveis**, **zero** recuperados nessa amostra.

### Execução #45051903271 — sombra de decisões LONG/SHORT sobre os mesmos dados

O mesmo tipo de coleta recuperou **47 ativos OKX** e comparou as políticas nos mesmos dados:

| Métrica | Base original — sombra | Receipt — pesquisa |
|---|---:|---:|
| Tickers elegíveis na primeira etapa | **47** | **47** |
| Símbolos com OHLCV validados nesta coleta | **47** | **47** |
| Planos de entrada condicionais confirmados | **0** | **0** |
| Ordens enviadas | **0** | **0** |
| Lucro comprovado | **Não** | **Não** |

Timestamps da execução #45051903271:
- `scan_started_utc` = **2026-11-22 17:52:58.210 UTC**;
- `ticker_fetch_started_utc` = **17:52:58.578 UTC**;
- `ticker_fetch_completed_utc` = **17:52:58.806 UTC**;
- `decision_data_ready_utc` = **17:53:38.232 UTC**.

O tempo entre início do scan e snapshot pronto ficou em cerca de **40,022 segundos**. É tempo da execução experimental, **não** latência de fila do cron nem tempo de publicação/alerta.

**A falha de timestamp não ocorreu nesses dois scans completos.** Portanto, não se deve alegar que o modo receipt recuperou sinais no mundo real nessas duas rodadas. O ganho potencial de elegibilidade foi demonstrado em payload idêntico quando a situação ocorreu anteriormente, e as regressões reproduzem deterministicamente esse caso com ticker válido entregue após o relógio pré-fetch.

## Testes e artefatos

- `collector.py`: separação da rotina original de filtragem em `_filter_ticker_rows` e novo modo de relógio **opt-in** com a mesma resposta, além do carimbo `data_ready`. Modo legado não utiliza a nova referência de timestamp.
- `research/receipt_clock_validation.py`: trava de causalidade do snapshot real, rejeitando quotes após o recebimento e candles após scan start.
- `research/receipt_clock_shadow.py`: compara sob o mesmo snapshot a base de ativos que o filtro original teria admitido e os candidatos adicionais no modo receipt; nunca solicita ordem.
- `tests/test_ticker_receipt_clock.py`, `tests/test_receipt_clock_validation.py` e `tests/test_receipt_clock_shadow.py`: cobrem ticker novo válido, ticker futuro, ticker velho, clock regressivo, data-ready, candle fechado, shadow com mesmo dado, ausência de ordens.
- `.github/workflows/research-receipt-clock.yml` e `research-ticker-clock-shadow.yml`: duas validações independentes, read-only, com tests e artifacts de **7 dias**. O primeiro faz análise real; o segundo testa a mesma fonte e candidatos LONG/SHORT.
- Todos os **271** testes passaram na rodada concluída; testes anteriores fracassados durante o desenvolvimento foram corrigidos antes das rodadas aprovadas.

## O que não mudou e próximos critérios

**Não** foi alterado `master`, `market-data`, workflow de produção, alavancagem, taxa, stop, gatilho ou critérios de risco. **Nenhuma ordem foi enviada**; nenhuma informação individual de terceiros foi coletada; nenhum sinal lucrativo ou expectativa matemática positiva foi inferido. A investigação da disponibilidade de contrato perpétuo na MEXC permanece separada do parser OKX, sem fusão indevida de preços.

Antes de aprovar uma adoção em produção:
1. Executar shadow em **múltiplos horários**, especialmente junto às atualizações de ticker, e quantificar quantas vezes um ticker válido era falsamente descartado.
2. Confirmar que a mudança não aumenta os sinais de candle vencido nem cotação antiga e que a coleta/painel usam corretamente `decision_data_ready_utc`.
3. Medir a cadência de execuções agendadas separadamente: o defeito do relógio não explica por si só a discrepância anterior de runs do cron.
4. Comparar candidatos e saídas em novas amostras independentes, **após custos**, sem confundir maior cobertura com lucro.

**Decisão:** correção implementada e validada **exclusivamente na branch experimental**. **Ainda não liberar a flag em produção**, nem mesclar o PR, até confirmar comportamento fora de amostras pontuais.
