# Tema 3 — auditoria dos filtros pós-confirmação e ablação em 1m

**Executado em:** 2026-11-22 UTC · **Branch:** `test/systematic-trading-research` · [PR #3 (draft)](https://github.com/VinyRhyu/altcoin-scanner/pull/3).

**Verificação dos filtros:** [GitHub Actions #44989720195](https://github.com/VinyRhyu/altcoin-scanner/actions/runs/44989720195) — 3 jobs `success`, **133/133 testes**.  
**Comparação sombra com candles de 1 minuto:** [GitHub Actions #44989912352](https://github.com/VinyRhyu/altcoin-scanner/actions/runs/44989912352) — `success`, **138/138 testes**, coleta histórica 1m read-only.  
**Histórico de entrada:** **100 snapshots mais recentes no momento das execuções** de `market-data` (seleção móvel). A execução sombra guardou o commit de ponta e a lista ordenada de SHAs dos snapshots no artifact `filter-shadow-post-snapshot-1m`, retido por sete dias. Não pressupor que estas contagens coincidam com execuções anteriores em que os "100 mais recentes" eram outros.

## Objetivo e método

1. Reproduzir os sinais confirmados pelo motor `entry_trigger.py` **com dados e razões de bloqueio observados nos snapshots históricos**; não executar o motor sobre candles futuros.
2. Mapear os motivos textuais exatos para filtros estáveis, separando:
   - **Temporal / integridade:** tickSz ausente, sinal expirado, gatilho já atravessado, stop invalidado e perseguição do preço.
   - **Proteção de risco:** stop maior que 3 ATR e spread acima do limite.
   - **Estratégia:** R:R líquido mínimo, pivô antes do TP1, alinhamento 1h/2h e distância do gatilho.
3. Contar **menções** e **motivo único**, sem somar menções sobrepostas como se fossem candidatos independentes.
4. Simular, em separado, a **remoção de somente um filtro de estratégia** em candidatos sem qualquer outro motivo de bloqueio registrado. O candidato contrafactual continua `NAO_ENTRAR` na fonte. Nada é relaxado na produção.
5. Para esses candidatos e os aceitos pelo scanner, consultar somente OHLCV 1m **posterior ao snapshot**, descartando o minuto parcial; classificar possível acionamento e saída com os mesmos custos hipotéticos. Não inventar ordem intraminuto, bid/ask, funding ou fill.

O código está isolado em `research/filter_ablation.py` e `research/filter_shadow_1m.py`, com 16 testes novos em `tests/test_filter_ablation.py` e `tests/test_filter_shadow_1m.py`, além da suíte anterior. O workflow de comparação guarda evidências e a referência SHA da origem.

## Resultados de 100 snapshots: filtros

**262 configurações confirmadas:** BREAKOUT 244 (11 elegíveis condicionalmente); RETEST 18 (3 elegíveis). **14 gatilhos condicionais**, **248 bloqueios** e **205 candidatos com mais de um motivo**.

| Motivo registrado | Menções | Único bloqueio | Categoria | Candidatos hipotéticos ao remover apenas esse filtro |
|---|---:|---:|---|---:|
| Stop >3 ATR | 179 | 17 | proteção de risco | **0 — não removível** |
| Pivô estrutural antes TP1 | 170 | 15 | estratégia | **15** |
| Cotação já atravessou gatilho | 78 | 5 | temporal | **0 — não removível** |
| Distância do gatilho >0,9 ATR | 37 | 0 | estratégia | **0** |
| Tendência 1h contrária | 36 | 2 | estratégia | **2** |
| Tendência 2h contrária | 34 | 0 | estratégia | **0** |
| R:R líquido <1,6 | 25 | 4 | estratégia | **4** |

As menções acima se sobrepõem. **Apenas 21 de 248 bloqueios** mudariam de classificação registrada caso fosse ignorado isoladamente um dos filtros de estratégia investigados. Isto **não** significa que os 21 casos seriam negociáveis ou lucrativos.

**Assimetria descritiva:** stop >3 ATR: 158 menções SHORT, 21 LONG; pivô antes TP1: 134 SHORT, 36 LONG; distância >0,9 ATR: 37 SHORT, 0 LONG. Estes números podem refletir o regime/amostra e a construção do stop; não demonstram erro ou viés causal sem teste adicional.

## Comparação sombra de 1 minuto

**7.505 observações de 1m recuperadas em janelas históricas** (podem conter sobreposição e não equivalem a 7.505 timestamps únicos). Cenários limitados à validade da entrada após o snapshot e até a saída hipotética.

| Grupo | Candidatos | Saídas modeláveis | TP1 hipotético | Stop hipotético | Saída por tempo | Sem acionar | Outros / censurados |
|---|---:|---:|---:|---:|---:|---:|---:|
| Aceitos na fonte | 14 | **6** | 3 | 2 | 1 | 6 | 2 |
| Apenas pivô | 15 | **3** | 1 | 1 | 1 | 9 | 3 |
| Apenas R:R | 4 | **2** | 1 | 0 | 1 | 2 | 0 |
| Apenas tendência 1h | 2 | **0** | 0 | 0 | 0 | 0 | 2 |
| Apenas tendência 2h | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Apenas distância | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

**Detalhe dos "outros":** grupo aceito: 1 snapshot sem minuto completo até expiração e 1 cenário barrado por margem aproximada; pivô: 2 snapshots sem minuto completo e 1 gap de abertura além do gatilho; tendência 1h: 2 snapshots sem minuto completo.

**Zero fills reais comprovados** em todos os grupos. `MODELLED_TP1`, `MODELLED_STOP` e `MODELLED_TIME_EXIT` são desfechos hipotéticos do modelo de preço, não registro de ordem. Funding assumido, taxas/slippage de cenário e spread obtido no snapshot, não na execução. Se sequência dentro de 1m fosse indeterminável, seria rotulada como ambígua e excluída de "saída modelável".

## Interpretação e decisão

**Pivô antes do TP1** é o primeiro filtro que merece teste adicional porque aparece em 170 bloqueios e foi o motivo exclusivo em 15. Porém, ao ignorá-lo na pesquisa, **só 3 de 15** candidatos tiveram uma saída modelável no cenário de 1m — um TP1, um stop e uma saída temporal. Não há evidência para remover o filtro; sua eficiência estatística permanece indeterminada.

**R:R mínimo** foi o único bloqueio em quatro candidatos, dos quais dois tiveram saída modelável. Não é amostra suficiente para baixar o mínimo de 1,6. **Tendência 1h** não acrescentou sequer um caso com saída modelável. **Tendência 2h e distância** nunca foram o único motivo de bloqueio na amostra, de modo que removê-los isoladamente não aumenta os candidatos.

**Stop >3 ATR, preços atravessados e integridade dos dados** permanecem proteções. Não se recomenda eliminá-los para forçar contagem de sinais.

### Limitações científicas

- Este é um estudo **descritivo e observacional**, sem pareamento entre regimes, randomização, p-value, walk-forward ou análise de trades não observados.
- A auditoria não removeu filtros **de confirmação** de BREAKOUT/RETEST (corpo, volume relativo, fechamento além do nível); testou somente motivos de recusa **após** `CONFIRMADO`.
- A amostra é móvel de 100 snapshots e viesada pelo universo dinâmico da coleta. Uma comparação robusta exigiria congelar a seleção e testar várias janelas e moedas.
- Candles 1m recuperados *a posteriori* não contêm prints de ordens, bid/ask, profundidade de book, funding histórico ou liquidação real; resultados não são fills comprovados.
- Uma configuração pode ser detectada em múltiplos snapshots; o auditor retém a primeira observação por símbolo/fechamento/direção/método, reduzindo mas não eliminando dependência estatística.
- A lógica RETEST da base ainda usa ATR do candle atual para verificar breakout anterior, questão isolada em `fix/retest-atr-no-lookahead-20261121`. É preciso resolver essa inconsistência antes de atribuir performance aos filtros de RETEST.
- Um maior número de sinais ou desfechos hipoteticamente positivos **não** comprova expectativa matemática positiva.

## Resultado de engenharia

**Aprovar apenas o diagnóstico e os testes na branch de pesquisa.** Não modificar limites em produção, não fazer merge do PR draft nem executar ordens. Próxima auditoria recomendada: validar o cálculo de stop/R:R e a assimetria LONG/SHORT; depois estudar pivôs e tendências sob janelas walk-forward com dados de execução mais fiéis.

**`master` e `market-data` permanecem sem alteração por esta entrega.**
