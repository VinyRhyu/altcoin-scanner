# Correção experimental do relógio de tickers OKX — validação sem lookahead

**Data:** 2026-11-22 UTC. **Branch:** `test/systematic-trading-research`. **PR:** [#3 (draft)](https://github.com/VinyRhyu/altcoin-scanner/pull/3).

**Execução reproduzível:** [GitHub Actions #45051914172](https://github.com/VinyRhyu/altcoin-scanner/actions/runs/45051914172), **success** em ambos os jobs: 271/271 testes unitários e coleta real de pesquisa, cálculo de decisões e validação causal do tempo. A execução não publica em `market-data`, não envia ordens e não usa nenhuma chave privada.

## Erro que motivou a implementação

O `collector.py` do scanner original obtinha `now_ms` no início da varredura, antes de consultar instrumentos e tickers. O filtro exigia `0 <= now_ms - ticker.ts <= 300000`. Se `ticker.ts` atualizasse durante a própria requisição, mesmo uma cotação recebida legitimamente podia ser descartada por estar *depois* da referência antiga. A pesquisa anterior [#45050823381](https://github.com/VinyRhyu/altcoin-scanner/actions/runs/45050823381) demonstrou o defeito **com o mesmo payload**: 12 tickers elegíveis sob a referência pré-consulta versus 47 sob a referência do fim do fetch; 69 foram rejeitados apenas porque a cotação tinha timestamp posterior à referência pré-consulta, e zero estavam acima de cinco minutos de idade.

## Implementação (somente experimental)

O novo modo `RESEARCH_TICKER_CLOCK_MODE=receipt`, opt-in, faz o seguinte:

1. Registra `scan_started_utc`, `ticker_fetch_started_utc` e `ticker_fetch_completed_utc`.
2. Consulta instrumentos e tickers públicos **uma única vez**; aplica a **mesma função de filtro** tanto à referência antiga quanto ao instante de recebimento, preservando spread máximo 0,25%, ticker com até 300 segundos, volume mínimo e todas as outras regras. Registra as listas `shadow_added_symbols` e `shadow_removed_symbols`.
3. Não permite timestamp de ticker posterior ao **fim real da requisição**. Cotações efetivamente antigas continuam rejeitadas.
4. Mantém os candles limitados aos que já tinham fechado **até o início original da varredura**, mesmo que as cotações recebidas depois indiquem mais ativos elegíveis. Isso evita usar candles parciais ou futuros para decidir sobre o sinal.
5. Só no modo experimental, define `market_data.collected_utc` como o instante em que a **coleta completa já está disponível**, armazenado também em `research_clock_provenance.decision_data_ready_utc`. Esse horário deve ser posterior à obtenção da cotação e ao fechamento de todos os candles usados.
6. A decisão normal de `trade_decision.py` só é validada se `source_collected_utc` coincidir com o instante de disponibilidade e `evaluated_at_utc` for igual ou posterior.
7. O modo **padrão `legacy` não muda**: não lê relógios extras, usa o mesmo filtro e continua preenchendo `collected_utc` como antes. A produção não configura a variável experimental.

O script `research/receipt_clock_validation.py` **falha fechado** se detectar cotação obtida no futuro, candle fechado após o início do scan, metadata sem fuso, snapshots atribuídos a um instante anterior à disponibilidade ou decisão marcada antes dos dados. O script é separado do comportamento de produção.

## Resultados após implementação

| Etapa | Resultado |
|---|---|
| Testes completos do projeto | **271/271 PASS** |
| Coleta em modo experimental da OKX (dados públicos) | **47 ativos completos**, primeira tentativa |
| Comparação de elegibilidade no **mesmo payload** nessa coleta | **47 legacy; 47 receipt** |
| Ativos recuperados nessa coleta específica | **0** |
| Verificação do instante de disponibilidade | **PASS** |
| Verificação do fechamento dos candles antes do scan | **PASS** |
| Cotação futura ou velha demais autorizada | **Nenhuma** |
| Ordens executadas | **0** |
| Lucro ou expectativa matemática positiva comprovada | **Não** |

**Interpretação:** esta execução específica ocorreu fora de um período em que o relógio antigo rejeitasse os tickers, portanto o número não aumentou, e isso está correto. O caso de erro **12 → 47** havia sido reproduzido em pesquisas anteriores e agora existe teste unitário determinístico para o cenário, incluindo rejeição de cotações realmente antigas e de timestamps posteriores ao recebimento. Os 47 ativos completos não são 47 operações; nenhuma afirmação de P&L é possível.

## Código e testes

- `collector.py`: modo opcional `research_clock_mode="receipt"` no filtro e na coleta, comparação sob o mesmo payload e timestamp efetivo ao final.
- `tests/test_ticker_receipt_clock.py`: compara o legado com o modo opcional, respeita tempo e idade, preserva candles e falha fechado em clocks inválidos.
- `research/receipt_clock_validation.py`: valida o encadeamento completo dos relógios, do snapshot e da decisão sem acesso a conta pessoal.
- `tests/test_receipt_clock_validation.py`: 11 testes isolados incluindo timestamp do ticker após scan mas antes de recepção, rótulo incorreto de `collected_utc`, candle futuro, quote velho e ordem proibida.
- `.github/workflows/research-receipt-clock.yml`: dois jobs (regressões e dry-run efetivo da OKX), sem publicação nem negociação, artifact `research-post-fetch-clock-causality` com metadados, sem preços privados.
- `.github/workflows/research-ticker-clock-shadow.yml`: workflow shadow complementar, com a mesma filosofia read-only.

## Decisão de engenharia

**O problema do relógio foi corrigido experimentalmente e passou nas verificações de causabilidade**, mas NÃO foi promovido à `master` ou considerado solução de todo o scanner. Antes da adoção deve-se fazer monitoramento prospectivo mais longo e validar:
- Ocorrências reais de `shadow_added_symbols` em períodos de atualização e sua presença no universo final;
- Se o fechamento de candles anteriores ao scan e a hora de disponibilidade deixam a decisão efetivamente válida;
- Se o ganho de cobertura se traduz em qualidade de sinais e em resultado financeiro líquido em paper research (sem extrapolar de 47 tickers para expectativa lucrativa);
- Separadamente, a **cadência insuficiente do `schedule` GitHub Actions**, que não é corrigida por ajustar o timestamp do ticker.

**Sem alterar filtros operacionais, stop, ATR, volume mínimo, spread, código em `master` ou branch de dados `market-data`. PR #3 permanece draft.**
