# Comparativo A/B — stop de 3 versus 2 candles e rentabilidade hipotética

**Data da execução:** 22/11/2026 (UTC).  
**Repositório:** `VinyRhyu/altcoin-scanner`. **Branch experimental:** `test/systematic-trading-research`. [PR #3 draft](https://github.com/VinyRhyu/altcoin-scanner/pull/3).  
**Execução real da pesquisa:** [GitHub Actions #45017541916](https://github.com/VinyRhyu/altcoin-scanner/actions/runs/45017541916), `completed/success`, **157/157 testes**.  
**Regressão geral:** [#45017510861](https://github.com/VinyRhyu/altcoin-scanner/actions/runs/45017510861), todos os três jobs `success`.

## Objetivo

Comparar a versão **A** (stop original por três candles) à versão **B** (stop hipotético por dois candles), **sem diminuir o limite de 3 ATR nem desligar qualquer outro filtro**. Medir o possível efeito econômico simultaneamente ao desenvolvimento da técnica, **sem simular que candles de 1 minuto provam ordens executadas**.

### Congelamento do universo e método

Os resultados usam exatamente **100 snapshots reais** da branch `market-data` a partir do commit de origem imutável:

`0f4e1087a7acd0287a09b54893de36a9027904ca`

A origem está registrada em `reports/source_market_data_tip.txt` e em uma lista de arquivos de snapshot no artifact `matched-stop-ab-1m-financial-scenarios` (retido sete dias). Os snapshots foram recuperados em modo **somente leitura**, sem escrever na branch.

A pesquisa:
1. Reexecuta o mesmo motor de decisão, usando somente candles e ticker **disponíveis no momento arquivado**, com parâmetro explícito `research_stop_bars=3` (controle) ou `2` (alternativo). Todas as chamadas sem esse parâmetro continuam com **3 candles**.
2. Verifica que os **263 padrões confirmados** e os preços dos gatilhos são idênticos entre variantes. A versão B recalcula stop, TP e projeções de R:R e pivôs. Portanto, **um candidato pode deixar de ser elegível na versão B** apesar de o stop aproximar-se, por exemplo caso o R:R líquido piore após os custos fixos.
3. Mantém o filtro `risk_atr > 3`, tick oficial, spread, tempo máximo de validade de 15m, confirmação estrutural, tendência e todos os demais bloqueios. Não habilita sinais previamente barrados sem recalcular os filtros.
4. Consulta a OKX pública posteriormente, recuperando candles de 1 minuto apenas para a **união** dos candidatos que passaram em pelo menos uma das variantes. Dados históricos recuperados depois não representam capturas *point-in-time* de bid/ask.
5. Ignora o minuto já iniciado no instante do snapshot, não presume preenchimento ao preço exato em gaps adversos, censura entrada/stop/TP sem ordem temporal identificável, identifica insuficiência de dados e não conta entradas não acionadas como negociações ganhas ou perdidas.
6. Aplica cenário idêntico de **5 bps de taxa por lado**, **5 bps de slippage por lado**, spread **observado no snapshot como proxy**, funding assumido **zero**, alavancagem ilustrativa **20x**, até **16 candles de 15m** de manutenção. Não dispõe de profundidade de book, liquidação por mark price ou funding efetivamente liquidado.

**Não é um backtest de fill, nem rentabilidade comprovada.** O mercado e a execução podem não coincidir com os preços modelados.

## Resultados das mesmas 263 configurações

- **11** candidatos passam nos filtros em ambas as variantes;
- **3** passam apenas na versão A (3 candles);
- **6** passam apenas na versão B (2 candles);
- **243** não passam em nenhuma;
- **14** gatilhos condicionais na A, **17** na B;
- **20** chaves distintas de candidatos elegíveis na união;
- **4.499** observações de OHLCV 1m recuperadas em janelas que podem se sobrepor.

### Resultado líquido hipotético por cenário resolvido

| Métrica | A: 3 candles | B: 2 candles |
|---|---:|---:|
| Condicionalmente elegíveis | 14 | 17 |
| Saídas com sequência modelável | **6** | **7** |
| Sem saída resolvida / não acionados / barrados | 8 | 10 |
| Cenários de retorno líquido positivo | 4 | 3 |
| Cenários de retorno líquido negativo | 2 | 4 |
| Taxa de acerto somente nos resolvidos | **66,67%** | **42,86%** |
| **Média líquida em R**, somente resolvidos | **+0,68458867 R** | **−0,12168342 R** |
| Soma de R, somente resolvidos, sem carteira | +4,107532 R | −0,85178396 R |
| Profit Factor, somente resolvidos | **2,85568595** | **0,74764607** |
| Fills de corretora comprovados | **0** | **0** |

**Distribuição dos desfechos:**

| Desfecho | A | B |
|---|---:|---:|
| TP1 hipotético | 3 | 1 |
| Stop hipotético | 2 | 2 |
| Saída temporal | 1 | 4 |
| Sem ativação até vencimento | 6 | 9 |
| Snapshot sem minuto completo antes do vencimento | 1 | 1 |
| Barrado pelo buffer aproximado de liquidação | 1 | 0 |

No cenário puramente ilustrativo de **US$100 de risco de preço por operação modelável**, a soma seria **+US$410,75** para A e **−US$85,18** para B. **Não é resultado financeiro de uma conta**, nem percentual sobre capital: não foram simulados tamanho de posição real, bloqueio de margem, posições sobrepostas, liquidez, risco de portfolio, taxas de funding reais ou negociação em exchange.

Os retornos médios e win rates têm denominadores diferentes e **não devem ser comparados como um teste causal de performance**. As duas versões mudam o conjunto de entradas elegíveis e quais saídas são modeláveis.

### Comparação pareada estrita

Houve **11 candidatos elegíveis nas duas** versões, mas apenas **4** com saídas hipotéticas modeláveis em ambas; nesses quatro, **três tiveram o mesmo resultado em R** e um teve diferença mínima. Média pareada `B - A = +0,00153471 R`.

A diferença grande no resultado total das variantes decorre especialmente de **seleção de sinais, censura de cenários e tamanho de amostra**, não de evidência forte de que um stop melhora financeiramente um mesmo trade.

### Divisão cronológica exploratória (não é validação fora da amostra)

O comparativo incluiu dois grupos temporais fixos, separados por `70/30` do conjunto de timestamps elegíveis, apenas para verificar a estabilidade descritiva:

| Período | A: média líquida (n saídas) | B: média líquida (n saídas) |
|---|---:|---:|
| Primeiros `70% por tempo | +0,44925307 R (**4**) | −0,84383859 R (**4**) |
| Últimos `30% por tempo | +1,15525985 R (**2**) | +0,84119014 R (**3**) |

O resultado da versão B **muda de sinal** entre essas duas subamostras muito pequenas. Isso é um alerta contra generalização; não se deve chamar essa partição de *out-of-sample* independente, nem selecionar parâmetros a partir dela.

## Interpretação e decisão

**Implementação:** o comparativo passou e é reproduzível. O parâmetro opcional `research_stop_bars` foi adicionado à função experimental mantendo **3** como padrão. `research/point_in_time_candidates.py` aceita exclusivamente valores 2 ou 3; `research/stop_ab_profit_pilot.py` executa verificação de universo, orçamentos de API, paridade de origem e relatórios por método, direção e amostra pareada. Foram acrescentados nove testes determinísticos em `tests/test_stop_ab_profit_pilot.py`.

**Resultado econômico:** não há evidência nesta amostra que justifique mudar o stop padrão de três para dois candles. O B tem mais sinais elegíveis, mas média líquida negativa nas sete saídas resolvidas e regime instável. A tampouco teve expectativa lucrativa comprovada: **seis** saídas hipotéticas não permitem essa conclusão.

**Decisão:** manter **3 candles**, manter limite de **3 ATR**, todos os filtros e custos; a versão de **2 candles** permanece somente um braço de pesquisa, nunca adotada automaticamente.

**Passos para validação futura:** congelar novas amostras temporais independentes, aumentar a cobertura de diferentes símbolos/regimes, testar custos e funding reais ou proxies auditáveis, controlar operações sobrepostas e procurar evidência de lucro fora da amostra e com incerteza estatística. Não parametrizar com base nos mesmos 100 snapshots e depois usar essa mesma janela para reivindicar lucro.

**Integridade:** nenhuma operação financeira executada, nenhum merge, nenhuma alteração na `master` ou `market-data`.
