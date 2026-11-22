# Cronjob.org, shadow do relógio, MEXC e início controlado da validação líquida

**Data:** 2026-11-22 UTC. **Research branch:** `test/systematic-trading-research`. **PR #3 draft:** https://github.com/VinyRhyu/altcoin-scanner/pull/3. **Sem merge, sem ordens, sem dados pessoais.**

## 1 — A correção da origem do agendamento

O operador informou que o **cronjob.org executa uma chamada HTTP a cada minuto**. Isso invalida qualquer interpretação anterior de que a proporção `2/64` de GitHub Actions com `event=schedule` no mesmo dia mediria a taxa de sucesso do cronjob.org.

**São três níveis distintos e ainda não vinculados individualmente:** (A) cronjob.org realiza uma chamada HTTP na cadência declarada de 60s; (B) o destino HTTP eventualmente dispara um job do GitHub, ou verifica a saúde de uma página, ou executa outra ação (destino específico ainda não inspecionado); (C) uma execução bem-sucedida do scanner publica novos arquivos na branch `market-data`. Nem toda chamada HTTP obriga ou comprova uma publicação.

**Evidência pública obtida:** na página recente de 100 execuções GitHub em `master`, há **52 `event=schedule`** e **48 `event=workflow_dispatch`**, em épocas diferentes. A presença de `workflow_dispatch` não comprova que foi cronjob.org quem disparou; `schedule` não contabiliza chamadas externas. A última publicação de mercado identificada no primeiro exame foi o commit `7ef2e41f1e35be31d608e4155bd241410ee7648a`, **2026-11-22 19:03:01 UTC**, com `manifest.collected_utc` **19:02:06.719 UTC**. Isso é evidência de publicação, não de que 1m de cronjob.org falhou. A auditoria precisa acompanhar a idade em cada check real, sem generalizar sua idade para outros horários.

### Implementação

- `research/external_cron_heartbeat.py`: interpreta o JSON oficial de `GET /jobs/{jobId}/history` da API REST do cronjob.org, medindo chamadas observadas, status, HTTP, desvio `datePlanned → date`, duração e gaps ENTRE registros observados. **Não declara minuto faltante como falha se o histórico é limitado.** Não grava nem propaga URL de destino, identificação do job, cabeçalhos, body ou credenciais.
- `research/publication_cadence_audit.py`: mede a idade **real da publicação** `market-data` em relação ao relógio do check, timestamp de coleta no manifesto, intervalo entre commits e diferença publicação-coleta. Sem atribuir a origem ao cronjob.org.
- `.github/workflows/research-external-monitor-and-forward.yml`: job de auditoria read-only consulta metadados públicos de Actions e publicação; consulta **opcional** ao histórico do cronjob.org somente se o administrador configurar o secret GitHub `CRONJOB_ORG_API_KEY` e a variável `CRONJOB_ORG_JOB_ID`. **Nenhuma configuração de cronjob.org foi alterada nesta pesquisa**. Histórico bruto permanece somente no diretório temporário do runner; o artifact contém contagens agregadas e exclusão deliberada de URL, tokens e headers.
- `tests/test_external_cron_heartbeat.py` e `tests/test_publication_cadence_audit.py`: testes de redaction, duplicidade, páginas incompletas, relógios futuros e separação de resultados.

Documentação oficial usada: https://docs.cron-job.org/rest-api.html. A API exige autorização Bearer (secret) e ID do job para recuperar histórico. Não enviar credenciais no chat nem registrar URL de destino contendo tokens no repositório.

**Limite:** sem secret/ID previamente autorizados e configurados, `cronjob_history_available=false`. Isso significa **histórico indisponível**, não `0/60 execuções`. Não há validação da pontualidade real de 60 segundos até ativação opcional.

## 2 — Três varreduras A/B com o MESMO payload

O modo de pesquisa `RESEARCH_TICKER_CLOCK_MODE=receipt` já compara o ticker a partir do clock de recebimento sem alterar a estratégia original, preservando o máximo de 5min, spread e volume, e o timestamp `data_ready`. `research/receipt_clock_shadow.py` confronta os sinais sobre o mesmo OHLCV completo; os ativos que falharam em qualquer pré-filtro ficam censurados, não são inventados.

**Lote real aprovado:** https://github.com/VinyRhyu/altcoin-scanner/actions/runs/45076691977

| Medida no lote de 3 varreduras | Resultado |
|---|---:|
| Varreduras concluídas | 3/3 |
| Ativos com OHLCV validados | 47 em cada varredura |
| Tickers elegíveis somados, repetições incluídas | 141 no modo original, 141 no receipt |
| Tickers recuperados no primeiro filtro | 0 |
| Gatilhos condicionais adicionais | 0 |
| Ordens executadas | 0 |

As três varreduras ocorreram em período muito curto e **não são amostras independentes de performance**. Uma correção temporal que só aparece durante atualizações dos tickers pode não apresentar diferença em cada mini-lote. A demonstração anterior do defeito `12 → 47` no mesmo pacote continua válida e tem regressão determinística.

Novo script `research/forward_shadow_summary.py` consolida até dez slots, registra censura, recusa duplicidades, preserva o viés de subconjunto do comparativo de OHLCV e **não inventa PnL**.

## 3 — MEXC como segundo provedor isolado

`research/mexc_ohlcv_provenance.py` implementa acesso público e delimitado ao endpoint oficial `GET /api/v1/contract/kline/ORCA_USDT`. Os tempos da API são **Unix seconds**, não milissegundos, os arrays `time/open/high/low/close/vol` são paralelos e o retorno pode conter **um candle no extremo final inclusivo** que não pertence à janela especificada. Agora o parser **descarta** explicitamente qualquer candle fora de `[start,end)` em vez de usá-lo.

**Amostra pública validada:** https://github.com/VinyRhyu/altcoin-scanner/actions/runs/45076762581.

| MEXC ORCA_USDT perpétuo | Candles completos | Lacunas internas | Candle fora da janela descartado |
|---|---:|---:|---:|
| `Min15`, 2026-11-22 10:00–22:00 UTC | **48** | **0** | 1 |
| `Min1`, 2026-11-22 20:00–22:00 UTC | **120** | **0** | 1 |

A integridade dos candles foi testada; **não se trata de captura point-in-time do livro de ofertas** e não permite reconstruir qualquer retorno executável de uma operação externa. MEXC e OKX permanecem separados. A listagem pública de `ORCA_USDT` foi validada por consulta anterior; não foi alterada a allowlist da OKX, nem inferido qualquer sinal.

Fonte: https://www.mexc.com/api-docs/futures/market-endpoints/get-candlestick-data. Sem API privada, sem chave de exchange, sem ordens.

## 4 — Protocolo para a próxima fase: resultado líquido em R

A pesquisa de rentabilidade **não pode ser aprovada com base nas três varreduras**, que tiveram zero gatilhos condicionais adicionais. O comparativo retrospectivo anterior de BTC/ETH/XRP em holdout tinha 21 desfechos modeláveis e soma **−5,97192591 R** com custos-base simulados; ainda não comprovamos edge positivo.

**Protocolo pré-definido antes de observar novos resultados:**

1. Coleção **prospectiva** em horários explicitamente observados: para cada sinal registrar exchange, símbolo, direção, método, instante real do preço recebido, instante de disponibilidade da decisão, timestamp dos candles fechados, cotação bid/ask e spread quando disponível. Não aceitar dados posteriores como se fossem anteriores.
2. Manter origens **separadas**: OKX/receipt e OKX/legacy usam o mesmo payload e entram em cohorts de pesquisa; MEXC só entra após confirmação de fonte de candles, contrato, ticker e custos próprios, sem misturar OHLCV.
3. Entrada e saída de paper research **após observação** com minuto inteiro e censura de ordem intraminuto ambígua. Sem prever fills reais. Incluir fee/slippage/funding modelados e alternativa adversa; para MEXC marcar custo **desconhecido** até verificar as taxas aplicáveis.
4. Reportar número de candidatos, armados, não armados, ambíguos, resolvidos, R líquido por classe, Profit Factor, drawdown, contribuição por stop/time/TP, frequência por lado e símbolo; bootstrap por dia ou regime, respeitando correlação.
5. Validar em **período cronologicamente posterior** sem ajustar filtros no mesmo holdout. Não declarar expectativa positiva se média/confiança/custos não sustentarem a conclusão. Nenhum upgrade de alavancagem ou stop com base no retorno bruto de um único exemplo.
6. Antes de qualquer aprovação em produção exigir dados forward suficientes, integridade temporal comprovada, taxas verificadas, ausência de execução real de ordens no experimento, e um plano de rollback. **Nenhuma meta de lucro foi fabricada.**

**Resultado esperado agora:** código de observabilidade e separação de venues pronto; avaliação de rentabilidade segue **não comprovada** até novas observações causais e custos. O monitor externo real depende da conexão do histórico privado sem divulgar chaves.


## Verificação adicional: execução com auditoria conjunta de publicação e de eventos

[GitHub Actions #45076972062](https://github.com/VinyRhyu/altcoin-scanner/actions/runs/45076972062) executou **292/292 testes aprovados** no job de regressão e as verificações independentes de agendamento/publicação. A auditoria às **22:18:33 UTC** registrou o último commit em `market-data` às **19:03:01 UTC**, com **195,548 minutos** de idade de publicação. O manifesto do mesmo ciclo indica início da coleta às **19:02:06.719 UTC**.

O histórico privado do cronjob.org **não estava configurado** nessa execução: `EXTERNAL_CRONJOB_HISTORY_UNAVAILABLE_NOT_ZERO`. Isso não significa falha nem sucesso dos 60 checks por hora, mas impede classificar o serviço externo como a origem da atualização atrasada. O monitor somente poderá medir as chamadas externas quando um administrador registrar o secret `CRONJOB_ORG_API_KEY` e a variável `CRONJOB_ORG_JOB_ID` no GitHub Actions, sem compartilhar credenciais no chat ou no PR. **A frequência de 1 minuto e o destino real no cronjob.org não foram alterados.**

O protocolo do ponto 4 está pronto, mas *não* podemos atribuir PnL aos 3 snapshots recentes: não houve gatilho adicional nem dados de fills, bid/ask histórico, funding realizado ou pagamento de taxas. A pesquisa permanece sem lucro demonstrado.
