# MEXC x OKX: cobertura do ORCA e falso descarte por timestamp de ticker

**Data da verificação:** 2026-11-22 UTC. **Branch experimental:** `test/systematic-trading-research`.  
**PR:** [#3 (draft)](https://github.com/VinyRhyu/altcoin-scanner/pull/3).  
**Resultado final:** [GitHub Actions #45050868351](https://github.com/VinyRhyu/altcoin-scanner/actions/runs/45050868351), **success nos 3 jobs e 245/245 testes**. Também: [#45050823381](https://github.com/VinyRhyu/altcoin-scanner/actions/runs/45050823381) (reprodução do bug), [#45050707458](https://github.com/VinyRhyu/altcoin-scanner/actions/runs/45050707458) (replicação inicial da instabilidade).

Nenhum dado de conta, posição pessoal, resultado individual, alavancagem de usuário, horário de operação ou alerta de terceiro foi gravado no repositório; apenas metadados **públicos de instrumentos e tickers**, e medidas temporais das consultas. Não há ordens, login ou API privada.

## 1 — Diferença de mercados: ORCA_USDT na MEXC, ORCA-USDT-SWAP ausente na OKX

- MEXC: o endpoint público `GET https://api.mexc.com/api/v1/contract/detail?symbol=ORCA_USDT`, **sem credenciais**, respondeu com `success=true`, `code=0`, contrato `ORCA_USDT`, `futureType=1` (perpétuo), `state=0` (habilitado), `quoteCoin/settleCoin=USDT`, `maxLeverage=50` (metadado público geral), `priceUnit=0.001`, `contractSize=1` e `apiAllowed=true`. Verificado no job `mexc-contract` da execução #45050868351. A página pública [MEXC ORCA_USDT Perpetual](https://www.mexc.com/futures/ORCA_USDT) também apresenta o contrato. O valor de leverage é do **metadado geral do produto**, não prova limite de uma conta individual ou condição antiga.
- OKX: no estudo anterior, o endpoint público de instrumentos com `instId=ORCA-USDT-SWAP` retornou `51001`, identificador não encontrado na verificação. Além disso, `ORCA` não pertence à lista curada `collector.CRYPTO_BASES` usada antes de qualquer detecção de LONG ou SHORT.
- **Conclusão de cobertura:** mercados diferentes, instrumentos diferentes. Nenhum replay do scanner da OKX deve usar preço ORCA da MEXC como se fosse uma ordem ou spread de OKX. Metadados de hoje não comprovam a listagem exata ou os preços em horários históricos.
- Fonte de API: [MEXC — Get Contract Info](https://www.mexc.com/api-docs/futures/market-endpoints/get-contract-info); estados e campos da API vêm da documentação pública.

## 2 — Reproduzimos o colapso de 47 para 0 tickers elegíveis em poucos segundos

O diagnóstico `research/okx_ticker_eligibility_funnel.py` reproduz, sem alterar o coletor, a **mesma precedência e os mesmos limites** de `collector.tickers`. Para cada ticker da API pública da OKX, atribui uma categoria única de rejeição, inclusive ausência da lista curada, instrument not-live, cotação fora do prazo, spread superior a 0,25% e volume estimado abaixo do mínimo de US$2 milhões.

### Primeira rodada, 17:39:57–17:40:13 UTC — #45050707458

| Amostra | Elegíveis antes do pré-filtro 15m | Rejeitados no filtro combinado de horário |
|---|---:|---:|
| 17:39:57 UTC | 22 | 38 |
| 17:40:05 UTC | **47** | 0 |
| 17:40:12 UTC | **0** | 89 |

Os **549 tickers brutos** existiam nas três consultas. O diagnóstico inicial ainda não separava tickers realmente antigos de tickers mais recentes que o início da própria coleta; por isso, esta primeira rodada é apenas evidência de oscilação e não prova a causa de cada rejeição.

### Segunda rodada, 17:41:12–17:41:27 UTC — #45050823381

Adicionamos subtipos de falha temporal, relógios separados de **início** e de **fim** da consulta pública e um replay do **mesmo payload**, sem baixar dados de novo nem flexibilizar os filtros.

| Amostra | Elegíveis com relógio antes das APIs | Cotações rejeitadas por terem timestamp **posterior ao relógio inicial** | Cotações realmente antigas (+5min) | Elegíveis com relógio de **fim** da consulta, mesmo payload |
|---|---:|---:|---:|---:|
| 17:41:12 | 47 | 0 | 0 | 47 |
| **17:41:20** | **12** | **69** | **0** | **47** |
| 17:41:27 | 47 | 0 | 0 | 47 |

Na amostra crítica, o horário de conclusão da consulta era apenas **328 milissegundos** posterior ao relógio inicial, e a troca da referência temporal **sem mudar uma cotação** permitiu classificar 47 em vez de 12 tickers como elegíveis. Regras de liquidez e spreads não foram modificadas.

Isso é evidência **direta de falso descarte de tickers por comparação contra um relógio anterior à obtenção dos próprios dados**. Não demonstra, por si só, que todos os casos históricos de 8–9 ativos tenham exatamente a mesma origem; faltam aquelas respostas completas de ticker e seus clocks.

### Terceira rodada, 17:41:42–17:41:57 UTC — #45050868351

O cenário intermediário não se reproduziu nesses três instantes: **47, 47 e 47** elegíveis, zero descartar temporalmente, e mesmo resultado com ambos os clocks. O caráter **intermitente** é compatível com atualização temporal do provedor perto do início/fim da chamada, não com mudança constante de parâmetros.

Em todos esses testes, o mínimo `MIN_VALIDATED=15` e os parâmetros originais permaneceram intocados. Quantidade elegível é contagem da **primeira etapa ticker**; isso **não** prova elegibilidade final dos candles, liquidez validada por 96 candles 15m ou confirmação técnica de um sinal.

## 3 — Onde exatamente está a inconsistência no coletor

No código original:

- `collector.main` calcula `now_ms=int(time.time()*1000)` **antes** de `eligible_bases()` e antes de chamadas de dados da OKX;
- `collector.tickers(allowed,now_ms)` busca `public/instruments` e só depois `market/tickers`;
- a filtragem rejeita se `not 0 <= now_ms - ticker["ts"] <= 300_000`.

Se a corretora devolve um ticker com timestamp **posterior à coleta inicial** — fato perfeitamente possível — essa cotação é falsamente rejeitada como "futura", embora possa já ter sido recebida de forma legítima. Na amostra confirmada, todos os **69** descartes por horário pertenciam a esse subtipo, não a cotações antigas.

Há outra sutileza: `collected_utc` também é inicializado **antes** do trabalho de coleta e não representa o instante em que os quotes já estavam disponíveis. Portanto, simplesmente substituir `now_ms` pelo horário do fim da chamada **sem** atualizar a proveniência do sinal poderia produzir um novo erro: atribuir a um snapshot antigo uma cotação observada depois, abrindo espaço para lookahead. **Não aplicar essa troca diretamente em produção.**

## 4 — Implementação de pesquisa entregue

- `research/mexc_public_coverage.py`: consulta GET pública MEXC, verifica correspondência exata ORCA_USDT, perpétuo, USDT, estado e parâmetros públicos; falha de API resulta em **desconhecido**, jamais contrato ausente; não lê dados individuais.
- `research/okx_ticker_eligibility_funnel.py`: espelha os filtros de tickers da OKX, conta motivo de exclusão **uma vez por ticker**, distingue "ticker após relógio pré-consulta" de "ticker realmente mais velho que 5min", e mede diferença caso o mesmo payload seja carimbado ao fim da requisição. Contrafactual é **somente pesquisa**, não atualiza o coletor.
- `tests/test_mexc_public_coverage.py` e `tests/test_okx_ticker_eligibility_funnel.py`: verificam credenciais ausentes, símbolo exato, resposta da API, estados desconhecidos, confrontam as regras originais de `collector.tickers`, rejeitam tickers antigos e demonstram que um ticker legítimo pode ser descartado pelo relógio pré-consulta.
- `.github/workflows/research-mexc-coverage-funnel.yml`: três jobs independentes, consultas públicas, três amostras de OKX em 15 segundos, artifacts de sete dias, validação de contagem e não envio de ordens. **245 testes aprovados** na execução final.
- Nenhuma alteração em `collector.py`, em parâmetros de filtro, na `master` ou `market-data`; nada foi mesclado. PR #3 permanece draft.

## Decisão técnica

**O erro do relógio do ticker merece correção prioritária em ambiente experimental.** Plano seguro:

1. Diferenciar no coletor os campos `scan_started_utc`, `ticker_fetch_started_utc`, `ticker_fetch_completed_utc`, `quote_source_ts` e o instante a partir do qual cada snapshot passa a ser utilizável para sinal.
2. Calcular idade de quote contra o instante **efetivo de recebimento** da resposta, sem aceitar timestamps realmente futuros em relação ao relógio do runner (salvo verificação explícita de skew) e sem afrouxar o limite de 5 minutos.
3. Garantir que candles usados para decisão estejam fechados **antes do instante real da decisão**, e que todo dado de ticker usado para entrada esteja disponível até então.
4. Rodar versões antiga e nova em modo shadow, com o mesmo payload, medindo quantos tickers e sinais reaparecem, quantos erros de relógio diminuem, e se há alguma perda de integridade.
5. Em paralelo, continuar diagnosticando por que o GitHub Actions registrou só 2 runs em 64 horários nominais numa janela anterior. Esse é problema **independente** do relógio do ticker e não foi corrigido.

A MEXC pode servir futuramente como **segundo provedor de pesquisa**, com parser de contratos, tick/volume/fees/candles próprios e auditoria de cobertura, sempre separado da engine OKX até provar equivalência temporal. Não colocar um símbolo MEXC diretamente no scanner OKX.

**Não está provada expectativa matemática positiva, execução real, nem que qualquer sinal externo seria encontrado pela estratégia.**
