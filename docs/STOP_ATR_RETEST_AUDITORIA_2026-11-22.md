# Auditoria do stop 3 ATR e correção causal do RETEST — 22/11/2026 (UTC)

**Branch experimental:** `test/systematic-trading-research` · **PR:** [#3 (draft)](https://github.com/VinyRhyu/altcoin-scanner/pull/3).  
**Evidência final:** [GitHub Actions #44991072306](https://github.com/VinyRhyu/altcoin-scanner/actions/runs/44991072306) — **success** nos três jobs, **148/148 testes** no Python 3.12.  
**Fonte:** 100 snapshots históricos arquivados na branch `market-data`; **tip imutável** `0f4e1087a7acd0287a09b54893de36a9027904ca`. O artifact `extended-market-data-audit` contém os relatórios, o tip e os nomes dos arquivos dos snapshots, com retenção de sete dias.

## Objetivos

1. Reconstruir exatamente a geometria de stop dos setups confirmados (sem trocar fórmula, limite ou custos).
2. Explicar quantos SHORT/LONG sofrem recusa por `risk_atr > 3`, usando denominadores de cada direção, não apenas contagens absolutas.
3. Corrigir a validação do **rompimento anterior** no detector de RETEST: ele deve usar o ATR disponível no fechamento do rompimento, não a volatilidade calculada com o candle de reteste.
4. Comparar detector antigo e corrigido **na mesma lista de snapshots**, preservando os sinais BREAKOUT e mantendo o PR sem merge na produção.

## Correção de ATR

Antes:

`is_retest(bars, side, current_atr)` confirmava o rompimento do candle `t-1` chamando `is_breakout(..., current_atr)`, onde `current_atr` inclui o candle `t`. Essa retroalimentação pode invalidar ou confirmar retrospectivamente um breakout histórico.

Agora:

- `entry_trigger.is_retest` chama `is_breakout` com `atr14(bars[:-1])` para o breakout de `t-1`.
- O toque e o fechamento de recuperação do **próprio reteste `t`** continuam usando o ATR corrente, conhecido ao seu fechamento.
- `trade_decision.setup` usa igualmente o limiar da volatilidade anterior para o breakout de `t-1`.
- O piloto retrospectivo (`research/retrospective_price_pilot.py`) deixou de usar um filtro corretivo redundante após a correção da função original.
- Regressões sintéticas para LONG e SHORT, inclusive um candle posterior com volatilidade muito diferente, confirmam que o breakout anterior não usa informação futura.
- A alteração existe **somente na branch experimental**, embora afete ali `entry_trigger.py` e `trade_decision.py`. A `master` e o pipeline de ordens não foram alterados.

### Comparação no mesmo histórico

| Métrica | Detector antigo | Detector corrigido |
|---|---:|---:|
| BREAKOUT confirmados | 244 | **244** |
| RETEST confirmados | 18 | **19** |
| RETEST LONG | 5 | **5** |
| RETEST SHORT | 13 | **14** |
| RETEST com gatilho condicional | 3 | **3** |
| Configurações confirmadas, todos os métodos | 262 | **263** |

**Impacto:** o detector corrigido recuperou **um RETEST SHORT confirmado anteriormente descartado**. Essa configuração permaneceu bloqueada pelos filtros de risco; não surgiu gatilho condicional adicional. Não houve perda de confirmações nem mudança em BREAKOUT nesta amostra. Os pilotos de SOL 30/60 dias foram executados novamente com êxito, sem mudar as contagens anteriormente verificadas.

O benchmark de 100 snapshots é uma amostra móvel e o SHA acima permite reproduzir o exato universo desta execução. Não misturar estas contagens com outras janelas de 100 snapshots.

## Diagnóstico dos stops

A estratégia define o stop como a opção mais afastada entre:

- **Componente de nível:** `nível de rompimento ±0,4 ATR`.
- **Componente de extremos recentes:** mínima ou máxima dos **últimos 3 candles** `±0,15 ATR`.

A posição do stop usa o tick oficial do símbolo e o sinal da direção; para LONG é o menor dos dois preços, para SHORT o maior. A distância medida a partir do gatilho, dividida pelo ATR, gera `risk_atr`. Somente `risk_atr > 3` dispara o filtro.

Todos os **263 stops** da auditoria foram reconstruídos e conferidos contra os stops originais, incluindo arredondamento por tick; **nenhuma divergência**. A comparação do filtro >3 ATR também foi conciliada com a auditoria anterior do motor.

### Taxas normalizadas por direção

| Indicador | LONG | SHORT |
|---|---:|---:|
| Configurações confirmadas com stop calculável | 51 | 212 |
| Stop >3 ATR | **21** | **159** |
| Proporção do respectivo lado | **41,18%** | **75,00%** |
| Mediana de `risk_atr` (todos os confirmados) | 2,83 | 3,55 |
| 90º percentil do risco (todos os confirmados) | 4,03 | 8,07 |
| Máximo observado | 4,80 | 9,77 |

A diferença é significativa **em magnitude descritiva**, mas não permite afirmar viés da implementação: há mais sinais SHORT (212 vs 51), universo dinâmico, concentração de regimes e possível repetição de eventos correlacionados. Não foi aplicado teste inferencial como se os 263 eventos fossem independentes.

### O que realmente define o stop?

**Em 263/263 configurações**, o stop escolhido foi determinado pelo **extremo dos últimos três candles**, não pelo componente de nível.

Dos **159 SHORT** bloqueados:
- **114** ultrapassaram 3 ATR apenas devido ao componente dos extremos recentes (o componente de nível, sozinho, não excedia 3 ATR).
- **45** ultrapassariam 3 ATR mesmo pelo componente de nível; ambos eram maiores que o limite.
- O extremo relevante estava em `t-3` em **110** casos, em `t-2` em **45**, e empatou em **4**. **Nenhum** dos SHORT bloqueados teve o extremo definido isoladamente pelo candle mais recente `t-1` desta janela de três candles, onde `t` representa o candle de confirmação e os offsets são `-3`, `-2`, `-1` em ordem do bloco de três candles. Para evitar ambiguidade de nomenclatura, estes offsets são **posições relativas ao fim da janela**, correspondentes aos candles mais antigo, intermediário e atual, respectivamente.
  
Nos **21 LONG** bloqueados, o componente recente foi o único que ultrapassou 3 ATR e seu extremo sempre veio do mais antigo dos três candles.

**Interpretação técnica:** a proteção está reagindo à distância entre o gatilho (no candle de confirmação) e máximas/mínimas estruturais anteriores. Em movimentos fortes essa distância pode ultrapassar 3 ATR com facilidade. Não sabemos ainda se diminuir o período de referência para dois candles seria uma melhoria: isso aproximaria o stop da entrada, potencialmente aumentando stop-outs e invalidando a estrutura.

### Por método

| Método e direção | Confirmados | Stop >3 ATR | Percentual |
|---|---:|---:|---:|
| BREAKOUT LONG | 46 | 21 | 45,65% |
| BREAKOUT SHORT | 198 | 148 | 74,75% |
| RETEST LONG | 5 | 0 | 0% |
| RETEST SHORT | 14 | 11 | 78,57% |

A assimetria aparece predominantemente em BREAKOUT, mas há poucos RETEST para conclusões próprias.

## Decisão e próximo experimento

**Concluído e validado:** correção causal do RETEST na branch experimental, reprodução do stop original, atribuição de cada excesso de risco ao nível ou aos três candles recentes, comparação dos detectores, suíte e CI.

**Não autorizado por esses dados:** baixar o limite de 3 ATR, trocar o stop por um preço mais próximo, habilitar sinais rejeitados, alegar maior taxa de acerto ou expectativa matemática positiva.

**Próxima hipótese:** comparar apenas em pesquisa os stops de dois e três candles, com o limite de 3 ATR mantido, usando o mesmo conjunto de configurações, testes em 1m e sensibilidade de custo; verificar se a perda potencial e a invalidação estrutural mudam. **Ainda não há evidência para alterar o stop.** Também é necessário validar outras janelas e regimes antes de generalizar o padrão LONG/SHORT.

**Nenhuma ordem, nenhum fill real comprovado. Sem merge na `master` nem publicação na `market-data`.**
