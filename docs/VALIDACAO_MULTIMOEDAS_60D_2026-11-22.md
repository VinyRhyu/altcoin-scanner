# Validação independente BTC/ETH/XRP — cenário financeiro 60 dias

**Data:** 2026-11-22 UTC. **Repositório:** `VinyRhyu/altcoin-scanner`.  
**Branch:** `test/systematic-trading-research` · **PR:** [#3 (draft)](https://github.com/VinyRhyu/altcoin-scanner/pull/3).  
**Execução completa:** [GitHub Actions #45018407502](https://github.com/VinyRhyu/altcoin-scanner/actions/runs/45018407502): **success** nos três jobs de moedas e na consolidação. **175/175 testes aprovados** em cada job da matriz.

## Questão

O scanner original (stop estrutural de **três candles**, limite **3 ATR**, filtros atuais) apresenta expectativa líquida positiva quando é avaliado em **outras moedas**, em um período cronológico predefinido e sob hipóteses de custos mais adversas?

**Resposta observada neste experimento:** **não**. As três moedas produziram média líquida negativa na metade temporal de avaliação. Não significa que esteja provado que a estratégia sempre perde; significa que os dados **não sustentam** uma afirmação de lucratividade generalizada.

## Protocolo congelado ANTES da execução

| Parâmetro | Definição |
|---|---|
| Moedas selecionadas | **BTC-USDT-SWAP, ETH-USDT-SWAP, XRP-USDT-SWAP** |
| Ativo utilizado como piloto anterior | SOL-USDT-SWAP, **excluído** deste teste entre moedas |
| Histórico total | 2026-09-22 00:00 UTC a 2026-11-21 00:00 UTC, último instante exclusivo |
| Exploração (primeiros 30 dias) | 2026-09-22 a 2026-10-22 00:00 UTC |
| Avaliação temporal (últimos 30 dias) | 2026-10-22 a 2026-11-21 00:00 UTC |
| Candles OKX por moeda | **5.760 de 15m + 1.440 de 1h + 720 de 2h = 7.920** |
| Total das três moedas | **23.760 candles históricos**; sem gaps e com dados fechados |
| Stops/filtros | **3 candles**, máximo **3 ATR**, demais filtros intactos |
| Tick sizes | Proxies históricos explicitamente assumidos: BTC 0,1; ETH 0,01; XRP 0,0001 — **não são tickSz históricos certificados** |
| Cenário-base | Taxas 5 bps/lado, slippage 5 bps/lado, spread hipotético 0,0141%, funding assumido zero |
| Estresse | Taxas 5 bps/lado, slippage 10 bps/lado, spread hipotético 0,05%, funding adverso **1 bp a cada 8 horas** para ambos os lados |
| Execução | OHLCV retrospectivo de 15m; entrada e saída no primeiro mesmo candle de 15m **censuradas** como ambíguas |
| Validade de entrada | Apenas primeiro candle 15m seguinte à confirmação; **scanner real pode observar o sinal somente depois do fechamento** |
| Máxima duração de saída | 16 candles de 15m (4h). Eventos que terminariam além da fronteira entre as janelas são **excluídos**, não rotulados com preços da próxima janela |

Uma vez escolhido este protocolo, **não** alteramos os parâmetros para melhorar os resultados. Este é um **teste de transferência temporal e entre ativos**, mas não um teste independente intocado no sentido estatístico estrito: estratégia e custos foram discutidos anteriormente e os criptoativos compartilham regimes de mercado.

Todos os candles foram obtidos depois do evento, usando API pública OKX em modo read-only. Não há dados de ordem, book, bid/ask, spread realizado, tick histórico certificado ou funding historicamente liquidado.

## Avaliação temporal — últimos 30 dias

**22/10/2026 a 21/11/2026 (UTC)**. Apenas saídas modeláveis entram na média líquida; configuração sem entrada, ambiguidade e insuficiência de dados **não** são transformadas em operações vencedoras ou perdedoras.

| Moeda | Gatilhos condicionais | Saídas modeladas | Cenários líquidos positivos | Média líquida por saída modelada |
|---|---:|---:|---:|---:|
| BTC | 11 | **10** | 2 | **−0,09167047 R** |
| ETH | 4 | **3** | 0 | **−0,69075335 R** |
| XRP | 13 | **8** | 3 | **−0,37287015 R** |
| **Conjunto** | **28** | **21** | **5** | **−0,28437742 R** |

### Resultado agregado base e sob custos mais severos

| Métrica | Custos-base | Custos estressados |
|---|---:|---:|
| Candidatos elegíveis | 28 | 28 |
| Saídas hipotéticas resolvidas | 21 | 21 |
| Não acionados | 7 | 7 |
| Positivos líquidos (resolvidos) | 5 | 4 |
| Negativos líquidos (resolvidos) | 16 | 17 |
| Win rate líquido (resolvidos) | **23,81%** | **19,05%** |
| **Média líquida condicional por saída resolvida** | **−0,28437742 R** | **−0,38036861 R** |
| Soma de R das saídas resolvidas | **−5,97192591 R** | **−7,98774074 R** |
| Profit Factor hipotético | **0,49862932** | **0,40902799** |
| Máxima queda de soma sequencial de R *(não carteira)* | 5,9719259 R | 7,9877407 R |
| IC bootstrap 95% de média R, cluster por dia de sinal | [−0,8504829, +0,2818122] | [−0,9526586, +0,1835983] |
| Fills reais comprovados | **0** | **0** |

As bandas bootstrap são **descritivas e não provam vantagem ou sua ausência**, porque há apenas **12 dias com saídas modeláveis**, dependência entre símbolos, pouca variedade de regimes, censura e proxies de execução. Ambos os intervalos contêm zero; não devemos afirmar expectativa estatisticamente negativa com confiança.

## Exploração — primeiros 30 dias

A amostra anterior à avaliação teve **5** configurações condicionais nas três moedas e somente **2 saídas modeláveis** (ambas de XRP).

| Métrica nos dois cenários resolvidos | Custos-base | Custos estressados |
|---|---:|---:|
| Média líquida | **+0,03295601 R** | **−0,08828967 R** |
| Profit Factor | 1,27346501 | 0,49486027 |

O volume de casos da primeira metade é **insuficiente para escolher parâmetros** ou afirmar que a estratégia se manteve positiva no treinamento. O sinal negativo no período de avaliação é o achado empírico mais importante.

## Validade dos desfechos

O módulo novo `research/multiasset_price_validation.py` usa o detector atual com análise somente de candles já fechados nos três timeframes. É mais conservador do que o piloto anterior ao **censurar entrada + saída atingidas no primeiro candle 15m**, mesmo que apenas um dos alvos tenha sido tocado. Modela stops com abertura em gap mais adversa, mas **ainda não é um motor de execução realista**.

**Limitação especialmente importante:** o preço do ticker histórico no instante da leitura (`fechamento + 1 minuto`) é aproximado pelo fechamento do candle. A simulação arma o gatilho no fechamento, potencialmente antes do momento em que o scanner teria disponibilizado o sinal. Essa divergência só será resolvida com observações autenticadas ou histórico intrabar com horários e latência explicitamente verificados. A simulação também não comprova spread, book, slippage realizado, liquidação, funding nem preenchimentos.

A seleção de 28 gatilhos e as 21 saídas resolvidas é **condicional às suposições**. Nenhuma das médias é retorno de conta real ou estimativa confiável de retorno de carteira com posições sobrepostas e margem.

## Engenharia e integridade

- `research/retrospective_price_pilot.py` expõe planos com candles futuros **apenas quando explicitamente solicitado** para pesquisa; comportamento padrão permanece idêntico.
- `research/multiasset_price_validation.py` exige datas fixas e contiguidade dos três timeframes, separa as metades temporais, impede que um trade de treino use futuros da avaliação, censura ambiguidade do primeiro 15m, aplica estresse simétrico de funding adverso, agrega resultado por moeda/método/lado e mostra incerteza bootstrap por dia de sinal quando a amostra permite.
- `tests/test_multiasset_price_validation.py` tem **11 testes de regressão novos** sobre causabilidade temporal, gaps, saídas ambíguas, estresse e casos sem execução; os testes totais do projeto atingiram **175/175** na execução reportada.
- Workflow `.github/workflows/research-multiasset-validation.yml` preserva os 23.760 candles baixados e relatórios de cada moeda em artifacts de sete dias, além do relatório combinado.

## Decisão de pesquisa

**Não aumentar a confiança na rentabilidade do scanner nem alterar o stop com base no resultado favorável de SOL.** O A/B anterior mostrou que encurtar o stop para dois candles não trouxe melhoria consistente. Agora, **a estratégia original de três candles apresentou média hipotética negativa nas três moedas novas** sob o protocolo de 60 dias.

**Próxima prioridade recomendada:** atacar a **fidelidade de execução** (preço real disponível no snapshot, atraso da armagem, spread/tick histórico e 1m no período do sinal), além de ampliar a amostra de períodos/ativos sem escolher parâmetros olhando o teste. Separar melhoria técnica de evidência de lucratividade.

**Nenhuma ordem foi enviada; nenhum lucro real foi registrado; nenhuma alteração foi feita na `master` ou na `market-data`. PR #3 permanece draft.**
