# Altcoin Scanner — Fase 1: coleta de mercado

Coleta pública **Bybit V5, mercado linear USDT**, sem API key, sem ordens e **sem sinais de negociação**. Filtra mercados por turnover 24h e seleciona até 20 ativos de altas, quedas e amplitude intraday (proxy de volatilidade, **não** detecção intraday de volume anormal). BTC e ETH excluídos. Ainda não verifica ranking de capitalização top 100, nem status via instruments-info; isso fica para a próxima etapa.

## Publicar no GitHub

1. Crie um repositório **privado** no GitHub, por exemplo `altcoin-scanner`.
2. Extraia este ZIP e envie **todo o conteúdo**, incluindo `.github/workflows/scan.yml`, para a raiz do repositório.
3. Abra **Actions → Collect altcoin OHLCV → Run workflow** para testar manualmente.
4. Abra o job `collect` para conferir erros. Em **Artifacts**, baixe `market-data-...` e inspecione `market_data.json`.
5. Com o workflow habilitado, o agendamento roda em torno de cada 15 minutos (GitHub pode atrasar ou pular execuções).

## Execução local

```bash
python -m unittest discover -s tests -v
python collector.py
```

A saída é `output/market_data.json`, contendo dados de 2h, 1h e 15m, com candles fechados e timestamps em milissegundos UTC. **Nenhuma chave, senha ou permissão de trade é necessária.**

## Importante

- O script precisa de acesso HTTP externo e pode falhar por bloqueios regionais ou indisponibilidade da API. Falha de coleta **não** vira sinal.
- O scanner ainda não analisa price action, score, stops, TP ou R:R. Isso exige implementar e testar um analisador em uma segunda etapa.
- GitHub Actions não envia automaticamente dados para ChatGPT. Integração posterior exige workflow adicional ou serviço de análise.
- O cron executa somente na branch padrão do repositório.
