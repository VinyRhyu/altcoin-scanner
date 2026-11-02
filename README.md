# Altcoin Scanner — somente coleta pública

O projeto consulta dados de contratos USDT perpétuos da OKX em 2h, 1h e 15m. **Não analisa sinais, não executa ordens e não envia notificações.**

## Arquivos necessários

- `collector.py`: coleta, checa velas fechadas, liquidez e validade dos dados.
- `.github/workflows/scan.yml`: executa a cada 15 minutos e publica em GitHub Pages.

Os arquivos anteriores `analyzer.py`, `replay.py` e `tests/` podem continuar no repositório, mas este workflow não os executa.

## Configuração

1. GitHub → Settings → Pages → Source: **GitHub Actions**.
2. Envie os dois arquivos acima para a branch `main`. Se o branch padrão tiver outro nome, ajuste `branches: [main]` no YAML.
3. Execute Actions → **Coleta OKX para ChatGPT** → Run workflow (ou faça commit na `main`).
4. Verifique ambos os jobs `collect` e `deploy`.
5. Consulte `https://vinyrhyu.github.io/altcoin-scanner/market_data.json`.

O GitHub Pages é público. Não publique segredos, tokens ou informações pessoais.

## Variáveis

- MARKET_PROVIDER=okx_swap: somente OKX perpétuos, nunca spot.
- UNIVERSE_SIZE=50: máximo de ativos com dados completos (não necessariamente 50 disponíveis).
- MIN_VALIDATED=15: exige pelo menos 15 ativos.
- MAX_PRESCREEN=90: pré-coleta de até 90 ativos com liquidez.
- MIN_VOLUME_USDT=2000000: volume de 24h para descoberta, não aprovação de operações.
- PRIORITY_VOLUME_USDT=5000000: faixa informativa, não aprovação de operações.

O universo ainda é limitado por uma lista curada de criptomoedas em `collector.py` e pela disponibilidade de contratos OKX. Isso é seleção de cobertura e qualidade da coleta, não julgamento de LONG/SHORT.

A URL é lida sob demanda pelo ChatGPT quando houver acesso. Esta configuração **não cria** uma tarefa de monitoramento do ChatGPT e não garante alertas automáticos. Em caso de falha do workflow, o JSON antigo pode continuar servido; confira `collected_utc`.
