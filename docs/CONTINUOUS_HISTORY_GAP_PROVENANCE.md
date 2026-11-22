# Continuidade histórica e origem observável das lacunas (15m/1h/2h)

## Objetivo

A auditoria de 100 snapshots detectou 8.524 candles ausentes no timeframe 15m, 294 no 1h e 0 no 2h (entre observações). Os números são específicos daquela amostra. Esta fase **não interpola preços**: separa cada série em segmentos contíguos e relaciona cada lacuna com a presença observada do ativo nos snapshots arquivados.

Código: `research/continuous_history.py`; testes: `tests/test_continuous_history.py`.

## Execução offline

Após obter os snapshots `market_data*.json` da branch `market-data`:

```bash
python -m research.continuous_history deep_snapshots \
  --min-bars 100 \
  --report reports/continuous_history_report.json \
  --segments-jsonl reports/continuous_segments.jsonl \
  --candles-jsonl reports/continuous_candles.jsonl

python -m unittest discover -s tests -v
```

O workflow `.github/workflows/research-audit.yml` refaz isso com os 100 commits recentes de `market-data` **somente leitura**, valida os invariantes do particionamento e publica os relatórios como artifacts por sete dias.

## Semântica de uma lacuna

Para um par `(moeda, timeframe)` com candles consecutivos observados em `t0` e `t1`, há `(t1-t0)/duration-1` candles não observados quando `t1-t0 > duration`.

A classificação olha **somente** snapshots cujo `collected_utc` está entre o fechamento do primeiro candle faltante (inclusive) e a abertura de `t1` (exclusiva), isto é, quando ao menos um candle faltante já deveria estar fechado:

- `no_snapshot_during_missing_interval`: não há snapshots arquivados nesse intervalo.
- `symbol_absent_from_snapshots`: os snapshots observados nesse intervalo não incluem a moeda no universo.
- `symbol_present_in_snapshots`: a moeda aparece nos snapshots do intervalo, mas a lacuna permanece no histórico reconstruído.
- `mixed_universe_presence`: foram observados ambos os estados.

**Essas categorias não provam a causa.** Uma moeda presente em um snapshot pode ter apenas candles antigos; ausência pode decorrer de filtro do universo, indisponibilidade, delistagem ou outro motivo; e ausência de snapshots não significa ausência de negócios na OKX. Há também viés de seleção temporal do universo.

## Contrato dos dados exportados

- `continuous_history_report.json`: resumo de cobertura, todas as lacunas, contagem por categoria, contagem por timeframe e metadados dos segmentos.
- `continuous_segments.jsonl`: um objeto por segmento, inclusive segmentos curtos.
- `continuous_candles.jsonl`: **somente** candles pertencentes a segmentos com pelo menos `--min-bars`; inclui `segment_id`, `segment_index`, `symbol`, `timeframe`, `open_ms`, OHLCV e `first_seen_utc`. Nenhum candle sintético é gerado.
- Um `segment_id` é estável dentro da amostra: `symbol:timeframe:first_open_ms`.
- `meets_min_bars` indica apenas tamanho mínimo, **não** qualificação completa para backtest de estratégia.
- `backtest_ready` permanece `false`: ainda faltam validação de seleção *point-in-time*, janelas cronológicas, funding, spread histórico, slippage, margem e tamanho amostral.

## Critérios de aceitação

1. A soma de candles em todos os segmentos coincide com os candles únicos normalizados.
2. `segmentos = séries + número de lacunas`, sem sequências duplicadas ou lacunas dentro do mesmo segmento.
3. A soma dos candles ausentes coincide com a auditoria original.
4. O universo é consultado apenas através de observações temporais realmente arquivadas.
5. Nenhum arquivo em `master` ou `market-data`, nem o coletor, gatilhos ou publicação, é modificado.

## Próxima fase (não incluída nesta entrega)

Depois de avaliar a cobertura por moeda e por janela cronológica, implementar um **backtest experimental** separado para BREAKOUT e RETEST. Usar fills após candle fechado, spreads/taxas/slippage, funding, limite de margem e regras conservadoras para candles intrabar ambíguos. Comparar com benchmark independente (Freqtrade) apenas após alinhar semântica dos sinais, custos e timestamps. Exigir validação walk-forward e ablação dos filtros antes de qualquer inferência de vantagem estatística.
