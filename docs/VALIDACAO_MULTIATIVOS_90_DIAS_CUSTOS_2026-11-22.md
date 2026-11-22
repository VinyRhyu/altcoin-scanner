# Teste financeiro em BTC, ETH e SOL Futures — 90 dias, três blocos e estresse de custos

**Data:** 22 de novembro de 2026 (UTC).  
**Branch:** `test/systematic-trading-research` · **PR:** [#3, draft](https://github.com/VinyRhyu/altcoin-scanner/pull/3).  
**Execução reproduzível:** [GitHub Actions #45018464132](https://github.com/VinyRhyu/altcoin-scanner/actions/runs/45018464132), **success**, coleta de três ativos, execução da suíte **178/178 testes** e relatório completo publicado em artifact `multiasset-90d-financial-scenarios` com retenção de sete dias.

## Questão e congelamento do estudo

Queremos saber se a rentabilidade hipotética observada em um estudo isolado de SOL persiste ao aplicar **os mesmos critérios de estratégia a outros ativos e a períodos separados**, com custos operacionais mais exigentes.

Conjunto congelado, definido **antes de consultar os resultados deste experimento**:
- Instrumentos: `BTC-USDT-SWAP`, `ETH-USDT-SWAP`, `SOL-USDT-SWAP` (perpétuos USDT da OKX).
- Histórico fechado UTC **22/08/2026 00:00:00 até 20/11/2026 00:00:00**, fim exclusivo, exatos 90 dias.
- Três blocos **30 dias**: 22/08–21/09, 21/09–21/10 e 21/10–20/11 (UTC, fim exclusivo).
- Por instrumento: **8.640 candles 15m + 2.160 candles 1h + 1.080 candles 2h = 11.880 candles**, total **35.640**, com fontes históricas de download posterior, proveniência explícita e zero gaps detectados.
- Não foram feitas alterações de parâmetros dos filtros para adaptar a estratégia ao ativo ou à amostra.

As séries e os metadados de continuidade estão nos artifacts `history-90d-btc`, `history-90d-eth` e `history-90d-sol`; o relatório agregado e metadados estão no artifact `multiasset-90d-financial-scenarios`.

## Premissas financeiras — não são custos efetivamente executados

| Componente | Base | Estresse |
|---|---:|---:|
| Taxa por lado | 5 bps | 5 bps |
| Slippage por lado | 5 bps | 12,5 bps |
| Spread total assumido | 0,025% | 0,080% |
| Funding | zero assumido | zero assumido |
| Alavancagem ilustrativa | 20x | 20x |
| Duração máxima | 16 candles 15m | 16 candles 15m |

Tick size **proxy estático NÃO autenticado**: BTC 0,1; ETH 0,01; SOL 0,01. **As mesmas configurações de sinal, entrada, stop e TP são reavaliadas com os custos maiores**, em vez de se escolher outro conjunto de sinais que poderia artificialmente melhorar o estresse.

**Atenção à causalidade:** o piloto reconstrói sinais retrospectivamente com candles fechados e usa o preço de fechamento do 15m como proxy de ticker "um minuto após o fechamento", mas considera ativação hipotética **imediata** ao fechar o candle. Isso **não reproduz** a latência real da coleta e pode incluir preços atingidos no primeiro minuto que não estariam disponíveis após o scanner observar o sinal. OHLC 15m não fornece a ordem exata dos eventos intrabar. Trata-se de um **cenário de preços retrospectivo, não de um backtest fiel de fills**.

## Resultado geral

| Indicador | Custos-base | Custos elevados |
|---|---:|---:|
| Planos condicionais reconstruídos | 45 | 45 |
| Cenários com saída modelável (sem fills verificados) | 35 | 35 |
| Sinais não acionados no próximo 15m | 10 | 10 |
| Média líquida por saída modelável | **+0,03381260 R** | **−0,11313262 R** |
| Mediana líquida | −0,17022878 R | −0,32376404 R |
| Taxa de acerto, somente cenários modelados | 40,0% | 34,286% |
| Profit Factor de cenário | **1,07617308** | **0,78922561** |
| Soma aritmética de R (não é carteira) | +1,18344111 R | −3,95964163 R |
| Fills reais comprovados | **0** | **0** |

**O resultado agregado ligeiramente positivo no cenário-base desaparece no cenário de custos elevados.** A diferença média de custo no mesmo conjunto de 35 saídas é **−0,14694522 R por saída**. Não é lucro realizável, retorno sobre a conta ou previsão de futuro.

Dos 45 planos, os desfechos de preço-base foram 9 stops, 7 TP1, 19 saídas temporais e 10 sem acionamento; não houve outros desfechos na amostra. Esses desfechos são **hipotéticos**.

### Por mercado

| Mercado | Planos | Saídas modeláveis | Média líquida base | Média sob custos elevados |
|---|---:|---:|---:|---:|
| BTC-USDT-SWAP | 11 | 9 | **−0,06582624 R** | **−0,21565584 R** |
| ETH-USDT-SWAP | 9 | 7 | **−0,63198343 R** | **−0,80324322 R** |
| SOL-USDT-SWAP | 25 | 19 | **+0,32630323 R** | **+0,18968229 R** |

BTC e ETH tiveram expectativa de preço negativa no subconjunto modelado em ambos os cenários; a positividade de SOL, mesmo sob custos mais altos, se baseia em **19 observações** e não demonstra estabilidade ou generalização. BTC, ETH e SOL são fortemente correlacionados; usar três ativos não elimina a dependência dos regimes de mercado.

### Divisão temporal pré-especificada

| Bloco UTC | Planos | Saídas modeláveis | Média base | Média estresse |
|---|---:|---:|---:|---:|
| 22/08–21/09 | 14 | 11 | −0,05139647 R | −0,20074980 R |
| 21/09–21/10 | 11 | 5 | −0,05594676 R | −0,21303055 R |
| 21/10–20/11 | 20 | 19 | +0,10676506 R | −0,03611795 R |

Os dois primeiros blocos têm média negativa no cenário-base e **todos os três são negativos sob estresse de custos**. A segmentação é retrospectiva e descritiva; **não** representa um holdout de produção realmente fora da amostra, nem amostras independentes com significância estatística.

## Engenharia, validações e integridade

- `research/retrospective_price_pilot.py` agora aceita qualquer símbolo válido `-USDT-SWAP` com nome de arquivo próprio (`btc_15m_90d.jsonl`, etc.), mantendo SOL como padrão legado. O estresse de custos reprecifica **exatamente os mesmos planos**, sem alterar sua elegibilidade.
- `research/multi_asset_validation.py` obriga três ativos, 90 dias completos e blocos fixos, agrega métricas por ativo, método, direção e período; rejeita melhora espúria do resultado líquido com custos maiores e não classifica censura como ganho ou perda.
- `research/okx_history_backfill.py` passou a repetir requisições somente em HTTP 429/5xx transitórios, com espera limitada. A primeira coleta de ETH teve HTTP 429; a execução final recuperou todas as séries integralmente. Não foram preenchidas lacunas.
- `tests/test_multi_asset_validation.py`, novas regressões no teste do piloto e do histórico OKX validam proveniência, tempo, pares de custo, ausência de fills e lógica de erro. **178/178 testes aprovados.**

### Limitações que impedem comprovar lucro

1. **Sem fills reais, bid/ask histórico, profundidade de book, tamanho de ordem e slippage efetivo**, não é possível transformar R de cenários de preço em PnL real.
2. **Sem financiamento real, mecanismo de liquidação por mark price nem posições sobrepostas modeladas**; o funding foi assumido zero em ambos os cenários.
3. **O começo da entrada é otimista** em relação à coleta do scanner; a resolução com 15m não consegue eliminar eventos que ocorreram antes do snapshot nem recuperar a sequência verdadeira dentro do candle.
4. **35 saídas hipotéticas no total**, menos de 100 por ativo; intervalo, dependência temporal e seleção retrospectiva tornam inferência de expectativa positiva inadequada.
5. As moedas pertencem ao mesmo universo e compartilham tendências; resultados não são replicações estatisticamente independentes.
6. O teste de 90 dias foi realizado após outros estudos de SOL. Apesar de fixado antes desta coleta, **não é um experimento prospectivo cego**.

## Conclusão de decisão

**Não foi demonstrada vantagem econômica robusta na estratégia atual.** O total tem média apenas **+0,034 R** no cenário-base e **−0,113 R** sob custos mais exigentes. O resultado de SOL não se replicou em BTC e ETH, e a variação por bloco também não é estável.

**Recomendação:** manter filtro, stop de 3 candles e limite de 3 ATR; não ativar operação real e não ajustar parâmetros para melhorar esses mesmos 90 dias. A próxima prioridade técnica deve ser **paper trading prospectivo com snapshots e preços 1m/ticks após a observação**, taxas/funding reais e avaliação de um período futuro ainda não visto. Depois, um backtest com controle de posições simultâneas e carteira pode medir retorno e drawdown com rigor.

**Estado de produção:** branch `master` intocada, `market-data` intocada, PR draft, nenhuma ordem enviada ou fill comprovado.
