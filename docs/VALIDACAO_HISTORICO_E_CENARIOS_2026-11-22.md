# Pesquisa de histórico e protótipo de simulação Futures — 2026-11-22 UTC

Branch: `test/systematic-trading-research`, originada de `master`. PR: [#3](https://github.com/VinyRhyu/altcoin-scanner/pull/3), **draft**. Todos os procedimentos são de pesquisa, somente leitura dos dados externos e sem negociação, secrets ou alteração do pipeline OKX.

## 1. Diagnóstico

Na amostra de 100 snapshots de `market-data` havia 114.961 candles distintos, 82 lacunas e 8.818 candles faltantes entre observações. Os 15m respondiam por 8.524 candles faltantes, frequentemente em intervalos onde a moeda não pertencia ao universo selecionado no snapshot. A maior sequência 15m tinha 350,5 horas (14,6 dias), nenhuma alcançava 30 dias. Mesmo após deduplicação, a série agregada não permite inferir performance robusta.

## 2. Hipótese

Uma coleta **separada** e paginada da API pública `GET /api/v5/market/history-candles` da OKX pode recuperar OHLCV temporalmente contínuo, com metadados de origem explícitos e sem misturar valores recuperados posteriormente com a decisão tomada em tempo real.

## 3. Implementação

| Arquivo | Finalidade |
|---|---|
| `research/history_depth.py` | profundidade, maiores segmentos, janela por ativo/timeframe |
| `research/okx_history_backfill.py` | exportação histórica limitada, paginação `after`, candles confirmados, volumes separados, sem escrever na `market-data` |
| `research/point_in_time_candidates.py` | somente confirmações observadas em snapshots reais, sem classificá-las como execuções |
| `research/futures_scenario.py` | cenários independentes de preenchimento, stop, TP1, saída temporal, custos, funding assumido e verificação aproximada de margem |
| `research/retrospective_price_pilot.py` | piloto retrospectivo SOL de 30 dias com apenas os 99 últimos candles fechados e MTF causal |
| `.github/workflows/research-sol-30d.yml` | coleta isolada de SOL 15m/1h/2h por 30 dias, validação e cenário retrospectivo |
| `.github/workflows/research-audit.yml` | suíte completa e auditoria de 100 snapshots |

**Correção de unidade:** para contratos perpétuos, `vol` da OKX significa quantidade de *contratos*, `volCcy` significa quantidade de moeda-base e `volCcyQuote` significa volume em USDT. Os módulos novos distinguem esses campos. O volume originalmente arquivado sob `row[5]` permanece o volume de contratos; o auditor de pesquisa agora nomeia `volume_contracts` em vez de `base_volume`, sem modificar `collector.py` de produção.

O exportador anota `point_in_time_eligible=false`, `retrieved_at_utc` e `backtest_ready=false`: nenhum dado recuperado posteriormente passa a ser automaticamente prova de preço executável em tempo real.

## 4. Validação reproduzível

### Suíte e histórico arquivado

- [GitHub Actions — 97 testes, todos aprovados](https://github.com/VinyRhyu/altcoin-scanner/actions/runs/44973893582).
- [GitHub Actions — sinais reais de 100 snapshots](https://github.com/VinyRhyu/altcoin-scanner/actions/runs/44973549125): 223 BREAKOUT confirmados (10 elegíveis condicionalmente); 18 RETEST confirmados (3 elegíveis condicionalmente), total 241 confirmações, 13 gatilhos condicionais. **Nenhum fill validado.**
- Metadados de tick/spread existentes nos snapshots são válidos **naquele snapshot**, não automaticamente no candle anterior.

### Histórico externo SOL

- [Coleta validada de 30 dias da OKX](https://github.com/VinyRhyu/altcoin-scanner/actions/runs/44973745352), de 2026-10-21T00:00Z a 2026-11-20T00:00Z, extremo final exclusivo.
- 15m: **2.880 candles contínuos em 29 páginas**.
- 1h: **720 candles contínuos em 8 páginas**.
- 2h: **360 candles contínuos em 4 páginas**.
- Todos os intervalos e timestamps extremos foram verificados, sem interpolação; 41 requisições no total. Artefatos disponíveis no GitHub Actions por sete dias (não publicados na `market-data`).

### Cenário retrospectivo de SOL

[Execução inicial do piloto retrospectivo](https://github.com/VinyRhyu/altcoin-scanner/actions/runs/44973844737) com ticker proxy = fechamento 15m, tick proxy = 0,01, spread proxy = 0,0141%, taxa = 5 bps/lado, slippage = 5 bps/lado, funding assumido = 0 a cada 8h, alavancagem nominal = 20x, máximo de 16 candles 15m:

| Método | Confirmações | Rejeitados pelas regras | Gatilhos condicionais |
|---|---:|---:|---:|
| BREAKOUT | 104 | 98 | 6 |
| RETEST | 11 | 8 | 3 |

Total de 2.585 fechamentos 15m avaliados com histórico suficiente nos timeframes superiores. **Esses resultados derivam de hipóteses retrospectivas e não de operações reais.** A amostra de 6 e 3 planos condicionais é pequena demais para conclusões de rentabilidade. R:R, lucro, Sharpe, Profit Factor ou taxa de acerto derivados desses cenários não são evidência de vantagem matemática positiva. Não misturar esta população retrospectiva de SOL com as 241 confirmações dos 100 snapshots multimoedas.

O replay foi posteriormente restringido aos últimos 99 candles de cada timeframe para igualar o horizonte do scanner. Validar o resultado do workflow mais recente antes de consolidar qualquer comparação quantitativa.

## 5. Limitações prioritárias

1. Sinais são gerados retrospectivamente com preço do fechamento como substituto do ticker um minuto depois. Gatilhos simulados como armados no fechamento podem ter ocorrido **antes da publicação do scanner**; 15m OHLC não permite comprovar a ordem temporal do fill.
2. Tick e spread históricos da SOL não foram reconstruídos. `0,01` e `0,0141%` são proxies ancoradas em um snapshot posterior, não comprovadas em cada data.
3. Funding histórico não foi coletado; o modelo usa um custo linear de cenário, não liquidações reais de funding. A margem/liquidação são aproximações grosseiras sem mark price ou book.
4. Se stop e TP1 tocassem no mesmo candle, o resultado é ambíguo. O simulador separa o pior caso e não o conta como trade confirmado. Não há book, fills parciais nem portfolio sizing.
5. O detector RETEST do código-base ainda compara o breakout anterior com ATR atualizado pelo candle de reteste. O piloto inclui um filtro retrospectivo com ATR anterior, mas não substitui a correção rigorosa já isolada na branch `fix/retest-atr-no-lookahead-20261121`.
6. 30 dias de **um ativo** e poucos candidatos não atendem critérios de múltiplos regimes, ativos independentes, walk-forward ou significância estatística.

## 6. Decisão e próxima ação

**Aprovados:** reconstrução, segmentação, medição de profundidade, exportação histórica, integração OKX real e simulador experimental com testes; sem alteração na produção.

**Não aprovados:** provar lucro, promover a estratégia, fundir PR na `master` ou automatizar ordens.

Próximo experimento: ampliar histórico para 90–180 dias em conjunto de símbolos definido de antemão e medir qualidade de preço/volume; coletar funding e metadados de tick/spread historicamente válidos; alinhar 1m ou trades com o timestamp real do scanner; integrar correção do ATR de RETEST; comparar BREAKOUT versus RETEST em splits cronológicos walk-forward e com sensibilidade de custos. Freqtrade poderá servir de comparação independente após alinhar regras e dados.

**Nenhuma ordem executada. Nenhuma escrita em `master` ou `market-data`.**
