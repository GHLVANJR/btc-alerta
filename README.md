# BTC Alerta (gratis)

Monitor simples do **Bitcoin** que roda no GitHub Actions a cada 30 minutos.
Usa so Python padrao e APIs publicas gratis. **Sem chaves.** So leitura, nunca envia ordens.

Quando algum gatilho dispara, o script:

1. Imprime o alerta bem claro no log do Actions
2. Faz o job **falhar de proposito** (`exit 1`) — o GitHub manda e-mail para o dono do repo
3. Se voce configurou, tambem manda no Telegram ou Discord

## Gatilhos e limites

| # | Gatilho | Limite | De onde veio |
|---|---|---|---|
| 1a | Vela de **1h fechada** (mediana das corretoras) | abertura→fechamento **>= 2%** | pedido do dono |
| 1b | **Pico de ATR** | ATR(3) de 1h / media do TR das ultimas 24 velas de 1h **>= 1,8** | rotina de volatilidade (`vol-alert`) |
| 2 | **Desvio de preco** entre corretoras | preco ou pavio da vela 1h **> 0,25%** longe da mediana | `consenso.py` |
| 3a | **Funding medio** | **> 0,03% por 8h** (para cima ou para baixo) | `funding_oi.py` |
| 3b | **Funding divergente** | maior − menor **> 0,02 ponto percentual** por 8h | `funding_oi.py` |
| 4 | **BTC 24h** | **+5% ou −5%** | pedido do dono |

Corretoras de preco: **Binance, Bybit, OKX, Kraken** (mediana). Se menos de 3 responderem,
entra a **Coinbase** como reserva. Funding: **Binance, Bybit, OKX**; se menos de 2 responderem,
entram **Deribit** e **Hyperliquid** como reserva. BTC 24h vem do **CoinGecko**
(reserva: velas de consenso).

### Para nao repetir

O `state.json` guarda o ultimo alerta de cada tipo. O mesmo tipo so alerta de novo depois do
tempo de espera — ou antes, se o valor ficar **50% pior**:

| Tipo | Espera |
|---|---|
| Vela 1h >= 2% | 1x por vela |
| ATR | 3h |
| Desvio | 2h |
| Funding medio / divergencia | 8h |
| BTC 24h | 24h |

O workflow faz commit do `state.json` sozinho.

## Como instalar

1. Crie um repositorio no GitHub (ex.: `btc-alerta`, pode ser **Public**)
2. **Add file → Upload files** e envie:

```
btc_alerta.py
state.json
README.md
.github/workflows/btc-alerta.yml
```

3. **Settings → Actions → General**: marque **Allow all actions** e **Read and write permissions**
   (precisa de write para gravar o `state.json`)
4. Aba **Actions** → **btc-alerta** → **Run workflow** para testar

O cron do GitHub **atrasa** (minutos a horas). E normal.

## Telegram / Discord (opcional)

Em **Settings → Secrets and variables → Actions → New repository secret**:

- `TELEGRAM_BOT_TOKEN` e `TELEGRAM_CHAT_ID` (crie o bot com o @BotFather)
- `DISCORD_WEBHOOK_URL`

Sem esses secrets, o alerta chega so por e-mail do GitHub. **Nunca cole tokens no codigo.**

## Mudar os limites

Todos os numeros ficam no topo do `btc_alerta.py` (`MOV_1H_PCT`, `ATR_RATIO`, `DESVIO_PCT`,
`FUNDING_EXTREMO`, `FUNDING_DIVERG`, `MOV_24H_PCT`, `ESPERA_H`).

## Endpoints (todos gratis / sem chave)

| Uso | URL |
|---|---|
| Binance preco/velas | `api.binance.com` (reserva `data-api.binance.vision`) `/api/v3/ticker/price`, `/api/v3/klines` |
| Bybit | `api.bybit.com/v5/market/tickers`, `/v5/market/kline` |
| OKX | `www.okx.com/api/v5/market/ticker`, `/market/candles`, `/public/funding-rate`, `/public/open-interest` |
| Kraken | `api.kraken.com/0/public/Ticker`, `/OHLC?interval=60` (par XBTUSDT, reserva XBTUSD) |
| Coinbase (reserva) | `api.exchange.coinbase.com/products/BTC-USD/ticker`, `/candles` |
| Funding Binance | `fapi.binance.com/fapi/v1/premiumIndex`, `/openInterest` |
| Funding Deribit (reserva) | `www.deribit.com/api/v2/public/ticker?instrument_name=BTC-PERPETUAL` (`funding_8h`) |
| Funding Hyperliquid (reserva) | `POST api.hyperliquid.xyz/info` `{"type":"metaAndAssetCtxs"}` (por hora ×8) |
| BTC 24h | `api.coingecko.com/api/v3/simple/price?ids=bitcoin&include_24hr_change=true` |

## Limitacoes (importante)

- O GitHub Actions roda nos **EUA**: **Binance** (`api.binance.com` e `fapi`) e **Bybit**
  costumam bloquear esses IPs. Por isso existem as reservas. Sem Binance Futures/Bybit, o
  funding pode vir so de OKX + Deribit/Hyperliquid
- Mistura pares **USDT** e **USD** (Kraken/Coinbase). A diferenca USDT/USD normalmente e
  bem menor que 0,25%, mas se o USDT desgrudar pode gerar alerta de desvio
- Com menos de 3 corretoras, o alerta de desvio **nao** dispara
- Cron a cada 30 min: um movimento rapido pode acontecer e voltar entre duas rodadas
- Nao e recomendacao de investimento

## Rodar no computador

```bash
python3 btc_alerta.py
```

Nao precisa instalar nada. Codigo `1` = houve alerta; `0` = tudo calmo.
