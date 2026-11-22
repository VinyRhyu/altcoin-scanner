# Etapas 1 e 2 — fidelidade de execução e diagnóstico de perdas

**Data:** 2026-11-22 UTC. **Branch experimental:** `test/systematic-trading-research`.  
**PR draft:** [#3](https://github.com/VinyRhyu/altcoin-scanner/pull/3).  
**Etapa 1:** [GitHub Actions #45019303052](https://github.com/VinyRhyu/altcoin-scanner/actions/runs/45019303052), `success`, **198/198 testes**, artifact `stage-1-2-execution-loss-audit`.  
**Etapa 2:** [GitHub Actions #45019320645](https://github.com/VinyRhyu/altcoin-scanner/actions/runs/45019320645), `success` nos 3 jobs de BTC/ETH/XRP e agregação; artifact `multiasset-60d-chronological-validation-summary` contendo o diagnóstico por tipo de saída.  
**Dados da Etapa 1:** **150 snapshots** do commit congelado `0f4e1087a7acd0287a09b54893de36a9027904ca` de `market-data`; lista dos 150 commits de origem preservada no artifact, 7 dias de retenção.

## Por que duas bases de estudo?

- **Etapa 1:** decisões autenticadas de scanner no horário de cada snapshot. Consulta histórica de OHLCV 1 minuto **posterior à observação**; descarta minuto em andamento. Este estudo possui mais fidelidade temporal de decisão, mas apenas 7 saídas modeláveis.
- **Etapa 2:** estudo retrospectivo com 60 dias completos de 15m/1h/2h de **BTC/ETH/XRP** (datas fixas 2026-09-22 a 2026-11-21 UTC, avaliação de 2026-10-22 a 2026-11-21 UTC). É útil para identificar a **contribuição financeira de cada rótulo de saída** com 21 cenários resolvidos, mas usa preços/ticks/spread hipotéticos no momento do sinal e arma o gatilho mais cedo do que uma varredura real. Não comparar diretamente o lucro das duas amostras como se fossem o mesmo backtest.

Todos os resultados são cenários numéricos. **Zero fills reais confirmados, nenhuma ordem enviada e nenhuma prova de expectativa matemática positiva.**

## Etapa 1 — diagnóstico com timestamp real e 1m posterior

| Medida | Valor |
|---|---:|
| Snapshots de origem | **150** |
| Configurações confirmadas | **324** |
| Gatilhos condicionais na fonte | **15** |
| Observações de candles 1m recuperadas | **3.505** (podem se sobrepor) |
| Saídas hipotéticas modeláveis | **7** |
| TP1 modelados | 3 |
| Stops modelados | 2 |
| Saídas temporais modeladas | 2 |
| Gatilhos não acionados até expiração | 6 |
| Snapshot sem minuto completo antes de expiração | 1 |
| Proteção contra liquidação aproximada do cenário | 1 |

**Medidas de latência de observação:**
- Mediana do atraso `snapshot observado − fechamento 15m`: **192,529 s** (aprox. 3min12s).
- Percentil 90 desse atraso: **612,270 s** (aprox. 10min12s).
- Mediana do tempo adicional entre observar e começar o próximo minuto OHLCV **inteiramente posterior à leitura**: **47,471 s**.
- Média da latência não pode ser confundida com latência da corretora: incorpora agendamento/coleta e alinhamento a minutos completos.

A medição de preço histórico não é tick-by-tick. Se o candle 1m da entrada tocar entrada e stop ou TP no mesmo minuto, o evento é marcado **ambíguo e sem retorno modelado**; o mesmo para stop+TP no minuto de saída. O spread é capturado no **snapshot**, não durante o suposto fill, e não há book ou funding histórico realizado.

**Resultados das sete saídas hipotéticas:**
- 4 positivas líquidas e 3 negativas líquidas; média **+0,56915269 R** somente nas 7 saídas modeláveis; Profit Factor **2,7048173**.
- Sob o estresse **predefinido**, média **+0,47821966 R**. Aumentou-se slippage de 5 para 10 bps por lado, spread proxy do snapshot em +0,0359 ponto percentual absoluto (3,59 bps), funding adverso assinado de 1 bp a cada 8h, mantendo taxa de 5 bps/lado.
- Por direção: LONG 2 stops, média **−1,10674223 R** (2 saídas); SHORT 3 TP1, 2 temporais, média **+1,23951065 R** (5 saídas).
- As 2 saídas por tempo tiveram uma positiva e outra negativa. Os 2 stops modelados ocorreram após mediana de 23 minutos da entrada modelada.
- MFE/MAE são calculados somente com minutos **inteiros interiores** entre minuto de entrada e de saída. Não se inventa caminho intraminuto. Esses extremos são **incompletos**, jamais prova de lucro realizável.

**Interpretação:** a janela de armagem de 15m pode se estreitar quando o sinal é observado alguns minutos após o fechamento. Aqui houve seis não acionamentos e um snapshot tarde demais para avaliar um minuto completo. **Não se pode afirmar que o atraso causou os stops ou não acionamentos sem teste contrafactual temporal no mesmo evento.**

## Etapa 2 — composição de R na avaliação de 30 dias de BTC/ETH/XRP

Todos os números desta seção são reproduções exatas dos relatórios por moeda do ensaio histórico fixo de 60 dias. No período de avaliação (2026-10-22..2026-11-21 UTC), foram **28 planos condicionais**, dos quais **21 desfechos de preço modeláveis** e 7 não acionados no primeiro 15m. Comparações com custos estressados mantiveram o mesmo caminho de preços e os mesmos 21 desfechos.

| Tipo de saída modelada | Quantidade | Resultado líquido conjunto base | Média por saída | Resultado sob estresse |
|---|---:|---:|---:|---:|
| Stop | 8 | **−9,23094031 R** | −1,15386754 R | −10,02481520 R |
| Tempo (16 candles de 15m) | 11 | **−0,80827898 R** | −0,07347991 R | −1,81317155 R |
| TP1 | 2 | **+4,06729338 R** | +2,03364669 R | +3,85024600 R |
| **Total** | **21** | **−5,97192591 R** | **−0,28437742 R** | **−7,98774074 R** |

**Achados:**
- Os **8 stops** contribuíram com a maior soma negativa, **−9,23 R**, e ocorreram em média após **6,875 candles de 15m** (duração **apenas hipotética**, pois a armagem imediata era um proxy).
- **8/11 saídas temporais** encerraram negativas após custos, **3/11 positivas**. Em média, o preço final havia avançado apenas **+0,06533607 R bruto em preço** desde a entrada hipotética. A média da **fração restante para atingir o TP1** era `0,97017244` (aprox. **97,0% do caminho inicialmente previsto**). Isso descreve progresso ao **encerramento**, não o melhor preço intraperíodo nem a ordem exata dos toques.
- Os **2 TP1** recuperaram hipoteticamente `+4,07 R`, insuficientes para contrabalançar as perdas de `−9,23 R` dos stops.
- Mudar para custos estressados reduziu a soma em **2,01581486 R**, ou cerca de **0,09599118 R por saída modelada**, e **um** cenário que era líquido positivo no base tornou-se líquido negativo sob estresse.
- A média hipotética líquida de `−0,284 R` não é lucro nem prejuízo real. O intervalo bootstrap exploratório por dia divulgado no estudo anterior cruza zero; não há evidência suficiente para estimar causalidade nem rentabilidade futura.

**Nota sobre comparabilidade:** o estudo de 1 minuto parte de **snapshots realmente observados**; o de 15 minutos é um **retrospectivo baseado em candles**, com ticker proxy de fechamento e sinal armado sem atraso real. Não se deve usar a diferença entre `+0,569 R` e `−0,284 R` como prova de que uma variável corrigiu a rentabilidade.

## Implementação entregue na branch de pesquisa

- `research/execution_loss_diagnostics.py`: sincroniza a decisão autenticada, janela de armagem após o snapshot, 1m público, custos-base/adversos, separa não execuções e eventos ambíguos e fornece indicadores incompletos de excursão favorável/adversa, tempo até entrada/saída, símbolo, direção e método.
- `research/retrospective_loss_attribution.py`: recebe os três relatórios históricos e reconcilia exatamente número de saídas, soma de R por motivo, custos extras e proximidade do TP ao fechamento temporal.
- `tests/test_execution_loss_diagnostics.py`: 10 regressões de causabilidade, minuto parcial, ambiguidade, spread, funding, proteção temporal e preservação de eventos censurados.
- `tests/test_retrospective_loss_attribution.py`: 10 testes de contribuições exatas, não acionamento, integridade de período e símbolos, custos e zero fills.
- `.github/workflows/research-execution-loss.yml`: auditoria reproduzível de 150 snapshots congelados no Actions com relatório e fonte.
- `.github/workflows/research-multiasset-validation.yml`: acrescentada a etapa de atribuição de perdas sem alterar resultados da pesquisa de preço.

## Decisão e próximas perguntas testáveis

1. **Não reduzir stop nem TP automaticamente.** O teste anterior de stop com 2 candles foi pior, e os stops atuais não estão comprovados incorretos.
2. **Priorizar qualidade e tempo da entrada.** Há atraso medido de observação e vários gatilhos que nunca acionam. Um novo teste deve avaliar se o horário de armagem e o primeiro preço negociável efetivo alteram o conjunto de oportunidades.
3. **Medir contexto/momentum antes de mexer em filtros:** por exemplo, volume relativo, distância do gatilho e estrutura 1h/2h para distinguir sinais que rapidamente atingem stop dos que não progridem para TP. Exigir comparação no mesmo evento e validação posterior em outros períodos, sem explorar o holdout anterior como banco de tuning.
4. **Aumentar fidelidade de execução:** gravar (futuramente, em coleta de pesquisa isolada) timestamps de ticker, melhor bid/ask, spread executável, mark price e funding, sem enviar ordens.
5. **Paper trading somente depois** de amostra e confiabilidade adequadas.

**Produção preservada:** nenhuma ordem enviada, nenhum merge, nenhum relaxamento de limite de risco, `master` e `market-data` intactas. PR #3 permanece draft.
