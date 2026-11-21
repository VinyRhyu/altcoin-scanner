# Resultado da validação experimental — 2026-11-21

Branch: `test/systematic-trading-research` (base: `master`).

## Execuções reproduzíveis

- [GitHub Actions — full-unit-suite + histórico real](https://github.com/VinyRhyu/altcoin-scanner/actions/runs/44937414500)
- [GitHub Actions — primeiro smoke com histórico real](https://github.com/VinyRhyu/altcoin-scanner/actions/runs/44937347182)
- [PR de pesquisa #3](https://github.com/VinyRhyu/altcoin-scanner/pull/3) (draft)

### Suíte completa do repositório

`python -m unittest discover -s tests -v` em GitHub Actions com Python 3.12:

- **49 testes executados e 49 aprovados**, cobrindo os 11 novos testes de auditoria e os testes já existentes do scanner.
- A execução valida comportamento dos componentes testados, não a lucratividade de sinais nem a ausência de todos os bugs.

### Smoke de 30 snapshots de `market-data` (somente leitura)

O job `historical-snapshot-smoke` obtém os 30 commits mais recentes da branch `market-data` com `git fetch` e extrai `market_data.json` de cada commit para arquivos locais temporários do runner. Nenhuma alteração é aplicada à branch `market-data`.

| Medida | Observado |
|---|---:|
| Snapshots válidos | 30 |
| Símbolos distintos | 67 |
| Candles normalizados únicos | 85.699 |
| Observações de candles repetidas, deduplicadas | 317.924 |
| Candles com versões conflitantes | 0 |
| Trechos com lacunas | 59 |
| Candles ausentes entre observações | 5.565 |
| Outras categorias de problemas reportadas | Nenhuma |
| `integrity_passed` | `false` |

A execução **passou no CI** porque o smoke exige capacidade de reconstrução e ausência de OHLCV inválido, snapshots inválidos, conflitos e séries ausentes. **A qualidade do histórico completo não passou**: `integrity_passed=false` por lacunas temporais. Essas são avaliações com critérios diferentes.

Os snapshots sobrepostos fornecem os candles repetidos (deduplicação intencional). Trechos com lacunas podem decorrer da cadência irregular de publicação e/ou da composição variável do universo de moedas. O teste não identifica a causa de cada lacuna. Git commit gaps não são, isoladamente, prova de candle gaps; a auditoria usa timestamps dos próprios candles.

Os arquivos `reports/market_data_smoke_report.json` e `reports/market_data_smoke_candles.jsonl` são disponibilizados como **artifacts** do workflow por sete dias. A execução pode ser repetida pela aba Actions; resultados futuros mudarão conforme novos commits sejam publicados na branch `market-data`.

## Diagnóstico

O módulo passa em seus testes e é capaz de processar dados reais. Foi confirmado um problema de cobertura para backtests contínuos na amostra testada; isso **não é uma falha da integridade de OHLC de cada snapshot individual**.

## Próxima hipótese e experimento

1. Investigar os 59 trechos, relacionando cada intervalo com os snapshots capturados e com a presença do ativo no universo em cada coleta.
2. Quando houver lacunas, não interpolar OHLCV. Separar séries contíguas por ativo/timeframe; excluir ou tratar explicitamente trechos incompletos na validação.
3. Somente com um histórico adequado: backtest de BREAKOUT e RETEST com custos, spread, slippage, funding, margem e política explícita de ambiguidade intrabar.
4. Walk-forward cronológico, controles de seleção de ativos, sensibilidade de custos e comparação com benchmark externo (Freqtrade) antes de inferir edge.

**Nenhum teste aqui demonstrou retorno positivo de qualquer estratégia. Nenhuma ordem foi executada.**
