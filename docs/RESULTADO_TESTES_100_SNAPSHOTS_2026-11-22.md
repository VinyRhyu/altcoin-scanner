# Validação ampliada de histórico — 2026-11-22 UTC

**Branch:** `test/systematic-trading-research`  
**Workflow:** [GitHub Actions #44970613992](https://github.com/VinyRhyu/altcoin-scanner/actions/runs/44970613992)  
**Status:** `success` nos três jobs (suíte completa, smoke de 30 e smoke ampliado de 100).  
**Escopo:** leitura de `market-data` sem alterar a coleta, produção, sinais ou executar ordens.

## Metodologia

O workflow extrai `market_data.json` dos 30/100 commits mais recentes da branch de histórico, reconstruindo candles fechados por `(symbol, timeframe, open_ms)` com `research.market_history_audit`. Identifica observações idênticas, versões conflitantes e lacunas internas em cada série por timestamp. Os relatórios são artifacts do GitHub Actions com retenção de sete dias.

## Resultados observados

| Métrica | 30 snapshots | 100 snapshots |
|---|---:|---:|
| Commits/snapshots analisados | 30 | 100 |
| Moedas distintas | 67 | 68 |
| Candles únicos | 85.699 | 114.961 |
| Observações repetidas, deduplicadas | 317.924 | 1.138.306 |
| Candles com OHLCV conflitante | 0 | 0 |
| Trechos internos com lacunas | 59 | 82 |
| Candles ausentes entre observações | 5.565 | 8.818 |
| `integrity_passed` | `false` | `false` |

### Localização das lacunas na amostra de 100

| Timeframe | Séries | Candles observados | Trechos com lacunas | Candles ausentes |
|---|---:|---:|---:|---:|
| 15m | 68 | 73.534 | 73 | 8.524 |
| 1h | 68 | 25.275 | 9 | 294 |
| 2h | 68 | 16.152 | 0 | 0 |
| **Total** | **204** | **114.961** | **82** | **8.818** |

O timeframe 15m concentra 8.524 de 8.818 candles ausentes (aproximadamente 96,7%). Não se deve interpretar 5.565 versus 8.818 como piora percentual: a janela de observação também aumentou. **A ampliação não produziu um histórico contínuo.** Não se atribuiu causa às lacunas (publicação, mudança de universo, delistagem ou dados de origem) com as evidências disponíveis.

### Testes automatizados

A suíte completa executou **49 testes, 49 aprovados** (Python 3.12). O GitHub Actions concluiu os três jobs com sucesso.

O sucesso do job de auditoria confirma leitura/validação de formatos, ausência de OHLCV inválido, ausência de conflitos e relatórios reproduzíveis; **não** significa que a reconstrução passou em `integrity_passed`, nem que qualquer estratégia tenha resultado financeiro positivo.

## Decisão técnica

**Não prosseguir para inferências de rentabilidade na série agregada descontínua.** Investigar a origem das 73 lacunas em 15m, registrar presença/ausência do símbolo em cada snapshot, e construir segmentos contíguos com critérios explícitos de elegibilidade. Jamais interpolar OHLCV para criar preços sintéticos de execução.

Somente depois: desenvolver backtesting de BREAKOUT e RETEST em janela cronológica, com spread, taxas, slippage, funding, risco de margem e política intrabar conservadora, comparando, se oportuno, com Freqtrade. Dados de funding/spread históricos não são garantidos pelo arquivo atual.

**Nenhuma ordem, nenhuma alteração em `master` ou `market-data`.**
