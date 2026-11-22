# Investigação do momentum, volume, direção e atraso de leitura

**Data:** 22/11/2026 UTC. **Branch experimental:** `test/systematic-trading-research` · [PR #3 (draft)](https://github.com/VinyRhyu/altcoin-scanner/pull/3).  
**Execução auditável:** [GitHub Actions #45024664897](https://github.com/VinyRhyu/altcoin-scanner/actions/runs/45024664897) — `completed/success`, **212/212 testes**, histórico 1m, extração causal de indicadores e auditoria do cronograma.  
**Universo congelado:** 150 snapshots de `market-data` anteriores/iguais ao SHA `0f4e1087a7acd0287a09b54893de36a9027904ca`, com arquivos e tip registrados no artifact `stage-1-2-execution-loss-audit` (retenção 7 dias). **Não** foi usado o último commit variável da branch de coleta.

## Objetivo e cautelas

Investigar se (a) o atraso efetivo de coleta e da primeira barra de 1m influencia a ativação do gatilho; (b) momentum, volume e tendência disponíveis **antes da entrada** distinguem os sinais que chegam a stop, TP1, saída temporal ou não entram.

Todas as características foram extraídas do **exato primeiro snapshot** em que o sinal aparece, usando apenas candles 15m, 1h e 2h já fechados até aquele momento. Candles de 1m baixados depois são utilizados **somente para classificar desfechos hipotéticos e cenários de atraso**, nunca para criar as características. Os filtros do scanner e as decisões continuam intactos.

**Amostra:** 324 configurações técnicas confirmadas, 15 condicionalmente elegíveis, 7 saídas modeláveis (2 stop, 3 TP1, 2 tempo); 6 não ativadas, 1 coleta tarde demais para completar minuto, 1 guard de liquidação aproximada. Dentre 324 confirmações, **281** tinham plano e spread computáveis (15 elegíveis e 266 bloqueadas); **43** não tinham plano/spread computável e não receberam métricas inventadas. Os 266 bloqueados **não** tiveram saídas de mercado simuladas nesta etapa e não entram em avaliação de rentabilidade.

## 1. Causa operacional potencial — agendamento e coleta

O workflow do scanner `.github/workflows/scan.yml` é programado para **01, 16, 31 e 46 minutos de cada hora UTC**, ou seja, nominalmente 60s depois do fechamento de candles 15m. O GitHub Actions pode atrasar jobs e o processo precisa realizar a coleta.

O novo módulo `research/scan_schedule_audit.py` inspecionou **150/150 snapshots**, usando a hora registrada em `collected_utc` e o fechamento modal dos últimos candles de 15m presentes no arquivo. Os 150 casos foram utilizáveis e não apresentaram exclusões.

| Indicador | Observado |
|---|---:|
| Alvo nominal de execução agendada após fechamento 15m | **+60s** |
| **Mediana do fechamento 15m até `collected_utc`** | **192,396s** (**3min12s**) |
| **Mediana excedente sobre o alvo nominal de 60s** | **132,396s** |
| Percentil 90 do atraso até `collected_utc` | **697,559s** (**11min38s**) |
| Coletas registradas até 3min após o fechamento | 13 |
| Coletas entre 3–6min | 104 |
| Coletas entre 6–10min | 12 |
| Coletas entre 10–15min | 21 |
| **Coletas ≥3min** | **137/150 (91,3%)** |
| **Coletas ≥10min** | **21/150 (14,0%)** |

Este atraso é **captura dos dados, NÃO entrega dos alertas**. Não mede diretamente fila do GitHub Actions, hora de início do job, tempo de API, processamento de cada símbolo, publicação em `market-data` ou o horário em que um usuário recebeu um sinal. Essas causas precisam de instrumentação adicional antes de serem atribuídas.

Nos **15 sinais condicionais**, a mediana foi `192,529s`, a p90 `612,270s`. As observações foram 1 antes de 3min; 11 entre 3 e 6min; 1 entre 6 e 10min; 2 entre 10 e 15min. Nos dois sinais com leitura ≥10min, não houve nenhuma saída modelável (um nunca acionou e outro não tinha minuto inteiro restante). Amostra insuficiente para afirmar que o atraso **causou** essas situações.

## 2. Contraste descritivo de volume, momentum e qualidade de candle

Os indicadores principais são:

- **Volume relativo 15m:** volume em USDT do candle confirmado dividido pela mediana dos 20 anteriores.
- **Momentum direcional 3 candles, em ATR:** variação do fechamento de 15m nos últimos três candles em favor da direção do sinal, dividida por ATR 14 calculado no snapshot.
- **Corpo relativo:** tamanho do corpo do candle confirmado dividido por sua amplitude high-low.
- **Distância restante até gatilho, em ATR:** distância entre ticker observado e gatilho no mesmo snapshot, não distância de um preço executável.
- **Contexto 1h/2h:** tendência do scanner na hora do snapshot, comparada à direção LONG/SHORT.

| Resultado hipotético | n | Mediana volume relativo | Momentum 3 candles (ATR) | Corpo relativo | Distância ao gatilho (ATR) |
|---|---:|---:|---:|---:|---:|
| Stop | **2** | **5,537** | 1,663 | 0,722 | 0,354 |
| TP1 | **3** | **3,697** | **2,277** | 0,492 | 0,648 |
| Saída por tempo | **2** | 3,367 | 1,688 | 0,691 | 0,445 |
| Nunca acionou gatilho | **6** | 2,199 | 1,575 | 0,579 | **0,757** |

**A diferença é descritiva, não preditiva:**
- Os stops ocorreram com volume **alto**, não baixo. Subir o mínimo de volume com base nesses exemplos seria uma hipótese sem sustentação.
- O momentum mediano foi maior nos três TP1 que nos dois stops, mas **não há amostra suficiente nem balanceamento** para descobrir um limiar.
- Candidatos que não acionaram tinham gatilho relativamente mais distante no snapshot (mediana `0,757 ATR`), consistente com menor probabilidade de alcance a partir do preço observado, mas igualmente não comprova causa ou novo limiar ótimo.
- Os indicadores foram extraídos corretamente antes dos candles que determinam o desfecho, evitando leakage temporal.

### Viés decisivo: LONG e SHORT não são comparáveis nesta amostra

| Direção | Condicionais | Stop modelado | TP1 modelado | Tempo modelado | Nunca acionou | Outro bloqueio/censura |
|---|---:|---:|---:|---:|---:|---:|
| LONG | 7 | **2** | 0 | 0 | 4 | 1 guard de liquidação |
| SHORT | 8 | 0 | **3** | 2 | 2 | 1 sem minuto completo |

**Nenhuma direção contém ao mesmo tempo um stop e um TP1 modelados.** Portanto, um contraste agregado “stop vs TP1” não identifica uma diferença de qualidade **independente da direção**. No conjunto de TP1 e stop, as tendências de **1h e 2h estavam todas alinhadas** à respectiva direção, logo “estar alinhado à tendência” também não distingue esses poucos resultados. O método e as moedas podem confundir ainda mais o contraste.

## 3. Teste pareado de latência adicional

Reavaliamos os **mesmos 15 candidatos**, usando os **mesmos candles históricos de 1m**, o plano e spread do snapshot congelados. O atraso adicional hipotético começa apenas do **primeiro minuto completo posterior ao snapshot real**, nunca antes. Foram predeclarados cenários de `0`, `+1` e `+3 minutos`.

| Deslocamento da possível armagem | Cenários com saída modelável | Sem saída resolvida/não acionados | Média líquida somente nos modeláveis |
|---|---:|---:|---:|
| Base: sem atraso extra | **7** | 8 | +0,569153 R |
| +1 minuto | **5** | 10 | +0,420791 R |
| +3 minutos | **6** | 9 | +0,335094 R |

**Comparação pareada apropriada:**
- **+1 minuto:** 5 candidatos têm saída modelável em ambos, 2 da base deixam de ser resolvíveis; média da diferença em R dos 5 pareados = **0,000000 R**.
- **+3 minutos:** 6 modeláveis em ambos, 1 deixa de ser resolvível; média da diferença em R dos 6 pareados = **+0,005012 R**.
- Não há candidatos censurados no cenário-base que passem a ter saída modelável nos cenários atrasados, nesta amostra.
- O deslocamento altera principalmente **quais entradas seriam ainda avaliáveis**, não o resultado dos mesmos trades modelados. A diferença entre as médias não é uma melhoria/queda causal de rentabilidade.

A reavaliação da estratégia em +1/+3min **não atualiza ticker, book, spread, validade do filtro no novo instante ou tamanho de posição**, portanto é apenas uma sensibilidade de atraso com **plano original congelado**, nunca um replay executável de uma ordem real. Um dos sinais inicialmente elegíveis pode passar a ser inválido pela expiração, e um trigger ultrapassado em gap é censurado.

## Evidência técnica entregue

- `research/signal_quality_audit.py`: extrai os recursos numéricos/contextuais fechados no primeiro snapshot, registra a composição por direção e método, casos tecnicamente confirmados porém sem plano, grupos de desfechos e sensibilidade pareada a 0/1/3min.
- `research/execution_loss_diagnostics.py`: conecta a extração causal ao mesmo conjunto de dados 1m anteriormente obtido, evitando novo download e divergências de fontes.
- `research/scan_schedule_audit.py`: mede 150 tempos de coleta em relação ao último candle 15m e ao alvo nominal de cron, sem afirmar identificar fila/latência de entrega.
- `tests/test_signal_quality_audit.py`: testes de candle futuro 1h rejeitado, snapshot mais antigo preservado, 1m posterior incapaz de alterar features, caso de expiração, atraso pareado, exclusão de candidatos sem plano e ambiguidade preservada.
- `tests/test_scan_schedule_audit.py`: testes de percentis, outliers, splits de timestamp e amostras malformadas.
- `.github/workflows/research-execution-loss.yml`: fixa a amostra por SHA, publica `execution_loss_diagnostics.json`, `signal_quality_diagnostics.json`, `scan_schedule_audit.json` e fonte, com assertions e retenção de sete dias.
- **212 testes aprovados** em [#45024664897](https://github.com/VinyRhyu/altcoin-scanner/actions/runs/45024664897).

## Decisão

**Não propor novos thresholds de momentum/volume nem alterar filtros/stop** com n=7 modelável, grupos totalmente desbalanceados entre LONG e SHORT e ausência de dados de execução. A evidência nova mais consistente é **o atraso entre fechamento 15m e `collected_utc` em 150 coletas**, que consome parte substancial da janela de 15m e merece engenharia de observabilidade.

Próximas tarefas orientadas a evidências:

1. Registrar na **branch de pesquisa**, futuramente, timestamps separados: despacho agendado, início do job, início/fim de cada chamada de dados, `collected_utc`, conclusão da análise, publicação e recebimento do alerta. Só então atribuir o atraso entre agendamento, coleta ou publicação. Um workflow disparado por cron no GitHub Actions não garante SLA de +60s.
2. Para cada sinal novo em paper research, capturar cotação bid/ask e spread executável em instantes reais após a decisão, validando tick/latência e seqüência intraminuto de modo mais realista.
3. Acumular resultados de LONG e SHORT com **stops e TP1 em cada lado**, controlar moeda/regime/método e depois comparar momentum e volume com incerteza; validar quaisquer mudanças em período posterior não usado para seleção.
4. Não fazer alterações operacionais na `master` sem validação controlada.

**Sem ordens, sem fills reais, sem modelo de lucro aprovado. `master` e `market-data` intactas; PR #3 ainda em draft.**
