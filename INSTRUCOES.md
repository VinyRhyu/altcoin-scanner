# Altcoin Scanner — coletor OHLCV (GitHub Actions)

Corrige o `HTTP Error 403` na API Bybit em runners hospedados: os IPs de hospedagem podem estar bloqueados por região (Bybit bloqueia IPs dos EUA, por exemplo). A nova coleta **não usa a Bybit**.

## Origens de dados (sempre sem login/chave)

1. **OKX USDT-SWAP** (perpétuos). Endpoints `/api/v5/public/instruments`, `/api/v5/market/tickers`, `/api/v5/market/candles`. O preço **não será idêntico ao da MEXC**.
2. **Binance Spot** (fallback). Origem `https://data-api.binance.vision/api/v3`: `exchangeInfo`, `ticker/24hr`, `klines`. **Spot não é perpétuo**; o relatório identifica claramente o mercado e nunca mistura candles entre fontes.

Configuração: `MARKET_PROVIDER=auto` (OKX, depois Binance). Também aceita `okx_swap` ou `binance_spot`.

## Atualizar repositório existente

1. Extraia o ZIP.
2. No GitHub, substitua **`collector.py`**, **`.github/workflows/scan.yml`**, **`tests/test_collector.py`** e **`README.md`**, preservando os caminhos e a pasta oculta `.github`.
3. Faça **Commit changes** na branch padrão.
4. Vá a **Actions → Collect altcoin OHLCV → Run workflow**.
5. Verifique a execução. Uma execução verde gera um artefato `market-data-<run_id>` com `market_data.json`. Em falha, o artefato conterá `collector_status.json` com o motivo por provedor.

O script exige pelo menos **15 ativos com 60+ candles fechados e contínuos** em 2h, 1h e 15m, dentre 20 candidatos líquidos. Valida timestamps, formato OHLCV, intervalo e atualização, e descarta candles abertos.

## Testes locais

```bash
python -m unittest discover -s tests -v
```

O coletor usa **apenas a biblioteca padrão** do Python 3.12; não precisa `pip install` nem secrets.

## Observações essenciais

- Os grupos de alta/queda e volatilidade da descoberta usam métricas de 24 horas. Um proxy de volatilidade **não prova anomalia intraday**; o `rel_volume_15m_20bar` é calculado só depois que os candles reais chegam.
- Os dados representam a **exchange escolhida**, não necessariamente sua plataforma de execução. Não use preços de spot como ordem em perpétuo, nem níveis de OKX como ordem da MEXC sem revalidação.
- Os dados são coletados, **não são sinais operáveis**. `signals_allowed: false` evita interpretar o snapshot como aprovação de entrada; score, stop, TP e R:R ainda precisam de uma camada independente de análise.
- GitHub Actions pode atrasar alguns minutos os horários agendados. O coletor não reutiliza dados antigos silenciosamente.
- O endereço `data-api.binance.vision` é documentado pela Binance para consultas públicas de mercado. Nem todo runner terá acesso: por isso existe fallback e diagnóstico. Nenhuma coleta online foi comprovada aqui; somente os testes locais foram executados.

Referências oficiais:
- Bybit: https://bybit-exchange.github.io/docs/v5/guide
- OKX: https://www.okx.com/docs-v5/en/
- Binance: https://github.com/binance/binance-spot-api-docs/blob/master/faqs/market_data_only.md

## Atenção ao custo de 15 em 15 minutos

Num mês de 30 dias, `*/15 * * * *` pode executar aproximadamente **2.880 vezes**. O GitHub Free inclui **2.000 minutos/mês para Actions em repositórios privados**, e a duração cobrada pode variar. Para começar com margem, considere trocar o `cron` para `0 * * * *` (a cada 1 hora) até verificar o tempo médio e seu limite de cobrança. Aumentar a frequência exige avaliar a franquia e as configurações de orçamento do GitHub.
