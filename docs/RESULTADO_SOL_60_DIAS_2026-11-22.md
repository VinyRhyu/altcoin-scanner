# Ampliação do piloto SOL-USDT-SWAP de 30 para 60 dias

**Data da execução:** 2026-11-22 UTC  
**Branch:** `test/systematic-trading-research`  
**Pull request:** [#3 (draft)](https://github.com/VinyRhyu/altcoin-scanner/pull/3)  
**Evidência principal:** [GitHub Actions — 60 dias #44974246177](https://github.com/VinyRhyu/altcoin-scanner/actions/runs/44974246177), conclusão `success`.  
**Suíte de regressão:** [GitHub Actions #44974232691](https://github.com/VinyRhyu/altcoin-scanner/actions/runs/44974232691), **99/99 testes aprovados**.

## Escopo

Manter apenas SOL-USDT-SWAP, os timeframes 15m/1h/2h e os mesmos parâmetros de cenário do experimento de 30 dias. Não estender para outros ativos, não ativar negociação, não editar `master` e `market-data`.

Período de 60 dias: **2026-09-21 00:00:00 UTC (inclusive) a 2026-11-20 00:00:00 UTC (exclusive)**. A janela de 30 dias anterior, de 2026-10-21 a 2026-11-20, continua disponível para comparação.

## Mudanças implementadas

- `research/retrospective_price_pilot.py` agora aceita `--days 60` e mantém o padrão `--days 30` sem quebrar reproduções anteriores.
- `tests/test_retrospective_price_pilot.py` adiciona dois testes de regressão para nomes de arquivos de 60 dias e validação do tamanho de janela.
- Novo workflow isolado `.github/workflows/research-sol-60d.yml`. Utiliza o exportador público da OKX com paginação limitada e faz a validação estrita de timestamps e proveniência antes do piloto.
- Dados completos ficam como artifacts temporários do GitHub Actions, não são publicados na branch de produção `market-data`.

## Resultado real da API OKX

| Timeframe | Candles em 30 dias | Candles em 60 dias | Páginas na coleta de 60 dias | Lacunas no período de 60 dias |
|---|---:|---:|---:|---:|
| 15m | 2.880 | **5.760** | 58 | **0** |
| 1h | 720 | **1.440** | 15 | **0** |
| 2h | 360 | **720** | 8 | **0** |
| **Total** | **3.960** | **7.920** | **81** | **0** |

O workflow confirmou explicitamente quantidades, timestamps de início/fim e passos consecutivos de cada timeframe. Não houve interpolação de preços. A coleta é *read-only*.

## Comparação retrospectiva de sinais

| Métrica | BREAKOUT 30d | BREAKOUT 60d | RETEST 30d | RETEST 60d |
|---|---:|---:|---:|---:|
| Configurações confirmadas | 104 | **222** | 11 | **21** |
| Planos condicionais | 6 | **13** | 3 | **3** |
| Confirmadas mas bloqueadas | 98 | **209** | 8 | **18** |
| Planos com desfecho calculável no cenário | 6 | **10** | 3 | **3** |

Fechamentos 15m avaliados com contexto multi-timeframe: **2.585 em 30 dias; 5.465 em 60 dias**.

Entre os **13 planos condicionais BREAKOUT** no cenário de 60 dias, **3 não atingiram o gatilho no candle seguinte**, 3 atingiram TP1, 3 tocaram stop e 4 foram encerrados pelo tempo máximo assumido. Os 3 planos RETEST foram encerrados por tempo. São resultados de um simulador, não ordens realizadas.

O período foi ampliado para trás mantendo o mesmo término. Não se deve interpretar os 13/3 planos de 60 dias como uma amostra de negócios independentes ou como demonstração de *edge*. Um aumento de confirmações não implica aumento comprovado de rentabilidade.

## Premissas e limites

- O ticker histórico é aproximado pelo fechamento do candle 15m (não é a cotação do instante em que o scanner teria publicado).
- Tick de 0,01 e spread de 0,0141% são *proxies*, não séries históricas confirmadas.
- Taxa hipotética de 5 bps/lado, slippage de 5 bps/lado, funding assumido como zero, alavancagem nominal 20x e saída máxima após 16 candles 15m.
- Execução intrabar, spread do instante do fill, funding real, marcação a mercado e liquidação não estão comprovados.
- A identificação dos sinais utiliza OHLCV histórico recuperado posteriormente. O campo `point_in_time_eligible=false` é preservado.
- Uma única moeda e apenas 13+3 planos condicionais simulados em 60 dias continuam insuficientes para prova estatística de vantagem positiva.
- Os dados recuperados **não** alteram o fluxo de publicação da `master` ou o conteúdo da `market-data`.

## Conclusão

**Ampliação de 30 para 60 dias concluída e validada.** Mantidos os experimentos anteriores e a segurança de produção. O desenvolvimento pode seguir para outros temas sem precisar estender o período novamente agora. A avaliação da rentabilidade real continua **não validada**, separada desta entrega.
