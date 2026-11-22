# Resultado: segmentos contínuos e evidência das lacunas (2026-11-22 UTC)

**Branch:** `test/systematic-trading-research`.  
**Execução validada:** [GitHub Actions #44972902071](https://github.com/VinyRhyu/altcoin-scanner/actions/runs/44972902071) — 3 jobs concluídos com `success`; Python 3.12, 60 testes aprovados.  
**PR:** [#3](https://github.com/VinyRhyu/altcoin-scanner/pull/3), mantido em rascunho.

## Diagnóstico e hipótese

A reconstrução de 100 snapshots de `market-data` (somente leitura) resultou em 114.961 candles únicos para 68 símbolos e três timeframes. Detectou 82 lacunas internas e 8.818 candles ausentes, sem versões OHLCV conflitantes.

Hipótese: as lacunas podem estar relacionadas ao universo dinâmico arquivado e à ausência de snapshots em certos períodos, em vez de OHLCV contraditório. A classificação implementada registra apenas as observações efetivamente disponíveis; **não determina causas**.

## Implementação

O módulo `research/continuous_history.py`:
- Reaproveita os candles validados por `market_history_audit.py`, nunca cria candles sintéticos.
- Quebra cada série em segmentos contínuos sempre que o intervalo entre aberturas supera o timeframe.
- Classifica cada lacuna conforme snapshots intermediários e presença observada do símbolo na seleção.
- Exporta sumário JSON, segmentos JSONL e candles JSONL de segmentos com duração mínima de 100 candles (parâmetro ajustável), preservando `first_seen_utc`.
- Informa `backtest_ready: false` mesmo quando um segmento satisfaz o critério de tamanho.

## Resultado real

| Métrica | Valor |
|---|---:|
| Snapshots arquivados | 100 |
| Símbolos | 68 |
| Séries (símbolo x timeframe) | 204 |
| Candles normalizados distintos | 114.961 |
| Candles OHLCV conflitantes | 0 |
| Lacunas internas / candles ausentes | 82 / 8.818 |
| Segmentos contínuos totais | 286 |
| Segmentos com ao menos 100 candles | 256 |
| Candles em segmentos com ao menos 100 | 111.991 |

### Segmentos por timeframe

| Timeframe | Série | Segmentos totais | Segmentos 100+ | Candles em segmentos 100+ | Lacunas | Candles ausentes |
|---|---:|---:|---:|---:|---:|---:|
| 15m | 68 | 141 | 121 | 71.554 | 73 | 8.524 |
| 1h | 68 | 77 | 70 | 24.582 | 9 | 294 |
| 2h | 68 | 68 | 65 | 15.855 | 0 | 0 |
| **Total** | **204** | **286** | **256** | **111.991** | **82** | **8.818** |

### Presença observada do símbolo durante as lacunas

| Categoria descritiva | Trechos |
|---|---:|
| `symbol_absent_from_snapshots` | 68 |
| `no_snapshot_during_missing_interval` | 14 |
| `symbol_present_in_snapshots` | 0 |
| `mixed_universe_presence` | 0 |

Os snapshots intermediários são considerados apenas após o fechamento do primeiro candle faltante e antes da abertura do próximo candle observado. A classificação **não prova** por que a moeda foi excluída nem se o mercado negociou nesse intervalo. É igualmente possível haver efeitos de seleção de universo e cadência dos snapshots.

## Validação

- `python -m unittest discover -s tests -v`: **60/60**. Adicionados 11 novos testes determinísticos: corte exato, ausência de interpolação, cardinalidade, limiar mínimo, classes de presença/ausência/mistas, exclusão temporal de observações precoces e arquivos exportados.
- Workflow de histórico de 100 snapshots: aprovados invariantes `segments = series + gaps`, soma dos candles originais, soma de candles ausentes e continuidade exata de timestamps dentro de cada segmento.
- Artefatos `extended-market-data-audit` (JSON e JSONL) ficam disponíveis no GitHub Actions por sete dias.

## Decisão e limites

**Aprovado:** módulo de reconstrução e segmentação para pesquisa offline.  
**Não aprovado:** backtesting de rentabilidade e seleção de sinais operáveis. 100 candles representam somente 25 horas de 15m; o limiar 100 serve para exclusão de fragmentos muito curtos, não garante amostra, múltiplos regimes ou contexto point-in-time.

Ainda precisam ser tratados: profundidade temporal, universo dinâmico, alinhamento causal entre timeframes, spreads e taxas históricas, slippage, funding, margem/liquidação, estatística robusta e walk-forward.

**Próxima ação:** medir comprimento e cobertura temporal dos segmentos por moeda e investigar se faz sentido buscar histórico complementar da OKX. Para não falsear desempenho, não se deve interpolar candles ausentes. Só então executar comparação experimental de BREAKOUT vs RETEST com protocolo de custos e execução explicitamente documentado.

**Nenhuma ordem foi realizada; nenhuma alteração na `master`, na `market-data` ou nos gatilhos de produção.**
