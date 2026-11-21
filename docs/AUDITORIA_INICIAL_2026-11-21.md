# Auditoria inicial — Crypto Futures Scanner (2026-11-21)

## Escopo e fontes

Exame das branches `master`, `experiment/non-exclusion-v2` e `market-data`.
Inspecionados o coletor, classificadores, gatilhos, decisão, replay, testes, workflows, manifest e resumo publicados. **Não houve execução de ordens.**

## Diagnóstico

- **Coleta:** `collector.py` coleta perpétuos USDT da OKX com candles fechados em 15m, 1h e 2h, checagem de OHLC, volume e gaps. A rotina auxiliar `early_warning.py` consulta 5m.
- **Contexto:** `experimental_scan.py` usa SMA20, inclinação, ATR14, suportes/resistências, volume relativo e pivôs confirmados. `score_experimental` é um score heurístico, não uma probabilidade calibrada.
- **Sinais:** `entry_trigger.py` detecta BREAKOUT e RETEST, projeta stop/TP e testa R:R, spread, distância do gatilho, obstáculos e tendência. `trade_decision.py` mantém LONG/SHORT por moeda e não executa ordens.
- **Replay:** `replay_entry_triggers.py` existe na branch experimental. Tem alinhamento temporal de 15m/1h/2h e avaliação posterior, mas não simula P&L líquido, funding, carteira, liquidação ou margem. Os metadados históricos de tick e spread são aproximados com o snapshot corrente.
- **Teste:** `unittest` cobre motores e sincronização; não foram identificados testes independentes de lucratividade ou walk-forward.

## Amostra de dados publicada

Manifest consultado em `market-data`: coleta em 2026-11-21T11:48:09.946Z, universo de 50 ativos, 99 candles por timeframe principal. Em oito arquivos `coins/<símbolo>.json` examinados, não foram detectados OHLC inválido, gaps intrassérie ou candles ainda abertos.

Cobertura do snapshot por moeda: 15m = 24h45; 1h = 99h; 2h = 198h; 5m auxiliar = 8h15.

No histórico dos 100 commits mais recentes de `market-data`, entre 2026-11-08T04:33:59Z e 2026-11-21T11:49:20Z, foram identificados 46 intervalos entre commits maiores que 30 minutos. **Intervalos entre publicações não equivalem automaticamente a gaps nos candles**: os snapshots se sobrepõem; é necessário reconstruir e verificar a série antes de concluir sua cobertura.

## Replay publicado e limitações

Arquivo `experimental_replay.json` consultado na branch experimental (coleta 2026-11-08): 41 ativos, 10.660 avaliações, 107 confirmações, oito gatilhos condicionais (quatro `STOP_TOUCHED`, três `NOT_TRIGGERED_NEXT_15M`, um `UNRESOLVED_IN_AVAILABLE_WINDOW`).

Esses números **não provam expectativa positiva**: amostra de operações pequena, janela curta, seleção dinâmica de moedas, metadados não históricos e ausência de resultado líquido executável.

## Primeira melhoria isolada

Hipótese: no primeiro candle após a confirmação, se o preço abriu antes do gatilho, o gatilho foi atravessado e o alvo foi tocado **sem tocar o stop**, a ordem dos eventos relevantes é determinística (entrada antes do alvo). O replay original classificava tal cenário como `INTRABAR_AMBIGUOUS`.

Implementação: correção em `forward_outcome()`, mantendo ambiguidade quando o stop pode ter ocorrido antes do acionamento e quando stop e TP foram ambos tocados.

Validação: 10 testes unitários de regressão em cenário isolado. Código anterior: 7/10. Código corrigido: 10/10. **A suíte completa do repositório e um novo replay com dados reais ainda não foram executados.** Esta mudança corrige rotulagem; não comprova melhora de rentabilidade.

## Prioridades subsequentes

1. Reconstruir OHLCV histórico por símbolo + timeframe a partir de snapshots/mercado, deduplicar por timestamp, validar continuidade, fechamento e cobertura, registrar versões dos dados e o universo disponível a cada instante.
2. Montar simulador de ordens e posições com entradas executáveis, regras intrabar conservadoras, spread, fees, slippage, funding, tick/lot size, margem isolada, limites de exposição e liquidação aproximada.
3. Implementar comparações independentes de BREAKOUT e RETEST, com baseline sem filtros, ablação individual dos filtros (especialmente pivôs), separação cronológica treino/validação/teste e walk-forward.
4. Avaliar expectativa líquida em R e por operação, profit factor, drawdown, sensibilidade de custos, erros, distribuição por moeda/regime e estabilidade fora da amostra. Evitar selecionar estratégias com poucas operações.
5. Manter a coleta, agendamento e publicação existentes até que haja teste reprodutível justificando alterações.

**Status da hipótese financeira:** não demonstrada; nenhuma alteração nos critérios de entrada foi recomendada a partir deste replay.
