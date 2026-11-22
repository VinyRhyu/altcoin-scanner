# Tema 2 — validação intrabar de gatilhos observados (1 minuto)

**Execução:** [GitHub Actions #44975001909](https://github.com/VinyRhyu/altcoin-scanner/actions/runs/44975001909) — **success**.  
**Suíte:** 122/122 testes aprovados na mesma execução.  
**Branch:** `test/systematic-trading-research` · **PR:** [#3, draft](https://github.com/VinyRhyu/altcoin-scanner/pull/3).  
**Segurança:** GET público à OKX e leitura de `market-data`; nenhuma ordem ou escrita em produção.

## Pergunta operacional

Depois que o scanner observou um BREAKOUT/RETEST confirmado e habilitou um gatilho condicional, **os candles de 1 minuto subsequentes são compatíveis com acionamento do gatilho antes do vencimento e com um desfecho de stop/TP?**

O método não afirma se uma ordem foi realmente colocada ou executada. Não há *order book*, *tick-by-tick tape*, ordens, fills nem bid/ask históricos na amostra.

## Método implementado

Arquivos:
- `research/okx_history_backfill.py`: agora aceita `1m` sem adicionar este timeframe como exigência da auditoria dos snapshots em produção; dados marcados como recuperados *a posteriori*.
- `research/minute_execution.py`: máquina de estados conservadora para ativação após o snapshot, vencimento de 15m, entrada hipotética, stop/TP, encerramento por tempo e proteção aproximada de margem.
- `research/minute_archive_pilot.py`: seleciona apenas `GATILHO_CONDICIONAL` efetivamente registrado nos snapshots, busca janelas de 1m com orçamento limitado por candidato e publica evidências detalhadas.
- `tests/test_minute_execution.py` e `tests/test_minute_archive_pilot.py`: regressões determinísticas, incluindo pre-snapshot, abertura adversa, ambiguidade de entrada e saída, falta de dados, margem e separação BREAKOUT/RETEST.
- `.github/workflows/research-minute-execution.yml`: integra tudo em GitHub Actions sem tokens, ordens nem alterações nos fluxos de produção.

**Regra temporal:**
1. Usar o primeiro snapshot arquivado em que a configuração foi observada, com `first_observed_at_ms` registrado.
2. Descartar todo o minuto de OHLCV que já começou **antes** do timestamp observado; ativar na primeira abertura de minuto inteira posterior ou coincidente.
3. Deixar de considerar novas entradas quando chegar o timestamp de vencimento (fechamento do candle de confirmação +15m).
4. Quando um minuto contém a entrada **e** o stop ou TP, classificar `ENTRY_MINUTE_AMBIGUOUS`, sem alegar sequência intraminuto.
5. Depois da entrada modelada, stop e TP no mesmo minuto geram `EXIT_MINUTE_AMBIGUOUS`, também sem retorno confirmado.
6. Ausência de candles, timestamps duplicados, intervalo faltante ou proveniência inesperada interrompem o cálculo; não se interpola.
7. Gaps de abertura que ultrapassam o preço exato do gatilho são rejeitados, sem supor preenchimento ao preço ideal.
8. Para stop com gap adverso depois de uma entrada hipotética, usar o pior preço de abertura em vez de supor proteção perfeita no stop.
9. Stops mais distantes que o buffer aproximado de margem são barrados. Isto **não** substitui liquidação baseada em *mark price*.

O cenário usa 5 bps de taxa por lado, 5 bps de slippage por lado, spread **observado no snapshot** (não no fill), funding assumido como zero, alavancagem nominal 20x, manutenção aproximada 0,5% e encerramento após no máximo 16 candles de 15m da entrada modelada.

## Evidências reais — 100 snapshots de mercado

| Etapa | Valor |
|---|---:|
| Snapshots históricos | 100 |
| Configurações técnicas confirmadas | 241 |
| Gatilhos condicionais registrados | **13** |
| Observações de candles de 1m recuperadas | **3.254** |
| Saídas hipotéticas com sequência modelável | **6** |
| Sem acionamento até vencimento | **6** |
| Barrado pelo limite aproximado de margem | **1** |
| Fills reais comprovados | **0** |

As 3.254 observações de 1m incluem janelas distintas por candidato e podem se sobrepor. Não representam 3.254 candles necessariamente únicos no universo.

### BREAKOUT: 10 gatilhos condicionais

| Desfecho do modelo | Quantidade |
|---|---:|
| TP1 hipotético | 2 |
| Stop hipotético | 2 |
| Saída temporal hipotética | 1 |
| Gatilho não acionado durante validade | 4 |
| Barrado pela regra aproximada de liquidação | 1 |

### RETEST: 3 gatilhos condicionais

| Desfecho do modelo | Quantidade |
|---|---:|
| TP1 hipotético | 1 |
| Gatilho não acionado durante validade | 2 |

**0 casos ambíguos** apareceram nesta amostra sob o critério de 1m; o código possui testes para impedir que casos futuros sejam considerados fills certos. Seria incorreto apresentar "3 TPs e 2 stops" como estatística de operações efetivamente realizadas.

## Por que isto não é um backtest validado

- O preço tocou uma região dentro de um candle de 1m, mas não foi comprovada liquidez disponível, ordem enviada, preenchimento ou spread no instante de execução.
- O candle de 1m que contém o snapshot é descartado por inteiro. É intencionalmente conservador; pode perder gatilhos reais.
- O histórico de 1m foi obtido **depois** dos eventos; somente o **sinal e as condições no snapshot** são observações *point-in-time*.
- Não há funding real, ordens de liquidação, mark price, profundidade e slippage histórico; os custos restantes são de cenário.
- O detector RETEST ainda depende da correção de ATR histórico já isolada na outra branch. Não foi alterado em produção.
- O experimento é limitado a 100 snapshots e 13 candidatos elegíveis; não permite avaliar expectativa positiva, significância, Sharpe ou retorno escalável.
- Não comparar diretamente as 13 observações multimoedas dos snapshots com os 16 planos de SOL estimados nos 60 dias: metodologias e populações diferentes.

## Decisão técnica

**Aprovado:** pipeline experimental de validação sequencial usando 1m e timestamps reais de snapshots, com teste de regressão e evidência reprodutível.

**Pendente:** verificar histórico bid/ask e liquidez, funding e custos por momento, integração da correção ATR de RETEST, maior tamanho de amostra e auditoria de execuções com dados de maior granularidade onde disponível.

**Nenhuma mudança na `master` ou na `market-data`. PR mantém-se draft; nenhuma ordem executada.**
