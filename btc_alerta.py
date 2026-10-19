#!/usr/bin/env python3
"""BTC Alerta - monitor gratuito do Bitcoin. Somente biblioteca padrao do Python, APIs publicas, sem chaves.
Porta para stdlib: consenso.py (mediana multi-corretora), funding_oi.py (funding/OI) e a rotina de
alerta de volatilidade (ATR 1h). So leitura, nunca envia ordens. Sai com codigo 1 quando ha alerta."""
import json, os, statistics as st, sys, time, urllib.request

# ---- limites (vindos do codigo existente; veja README) ----
MOV_1H_PCT = 2.0         # (1) vela 1h fechada com |fechamento/abertura - 1| >= 2%
ATR_RATIO = 1.8          # (1) ATR(3) 1h / media do TR das ultimas 24 velas 1h >= 1,8 (rotina vol-alert)
DESVIO_PCT = 0.25        # (2) corretora desviando > 0,25% da mediana (consenso.py LIM)
FUNDING_EXTREMO = 0.03   # (3) |funding medio| > 0,03% por 8h (funding_oi.py EXTREMO)
FUNDING_DIVERG = 0.02    # (3) maior - menor funding > 0,02 p.p. por 8h (funding_oi.py DIVERG)
MOV_24H_PCT = 5.0        # (4) BTC 24h >= +5% ou <= -5%
# re-alerta: depois do tempo de espera, ou antes se o valor piorar 50% (mesma regra do radar.py)
ESPERA_H = {"mov1h": 1, "atr": 3, "desvio": 2, "funding": 8, "diverg": 8, "mov24h": 24}
PIORA = 1.5
N_VELAS = 30
PAUSA = 0.5
AQUI = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(AQUI, "state.json")
UA = "btc-alerta/1.0"


def get(url, tentativas=2, dados=None):
    h = {"User-Agent": UA, "Accept": "application/json"}
    if dados is not None:
        h["Content-Type"] = "application/json"
        dados = json.dumps(dados).encode()
    err = None
    for i in range(tentativas):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=dados, headers=h), timeout=15) as r:
                return json.load(r)
        except Exception as e:
            err = e
            time.sleep(PAUSA * 2 * (i + 1))
    raise err


def post_json(url, dados):
    req = urllib.request.Request(url, data=json.dumps(dados).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": UA})
    try:
        urllib.request.urlopen(req, timeout=20).read()
    except Exception as e:
        print(f"  aviso: notificacao falhou ({e})")


def notificar(texto):
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if tok and chat:
        post_json(f"https://api.telegram.org/bot{tok}/sendMessage", {"chat_id": chat, "text": texto})
    hook = os.environ.get("DISCORD_WEBHOOK_URL")
    if hook:
        post_json(hook, {"content": texto[:1900]})


# ---------------- preco + velas 1h por corretora (porte do consenso.py) ----------------
def vela(t, o, h, l, c):
    return dict(t=int(t), o=float(o), h=float(h), l=float(l), c=float(c))


def binance(n):
    erro = None  # api.binance.com bloqueia IP dos EUA (GitHub Actions); data-api.binance.vision e o espelho publico
    for base in ("https://api.binance.com", "https://data-api.binance.vision"):
        try:
            p = float(get(f"{base}/api/v3/ticker/price?symbol=BTCUSDT")["price"])
            d = get(f"{base}/api/v3/klines?symbol=BTCUSDT&interval=1h&limit={n + 1}")[:-1]  # ultima em aberto
            return p, [vela(*x[:5]) for x in d]
        except Exception as e:
            erro = e
    raise erro


def bybit(n):
    p = float(get("https://api.bybit.com/v5/market/tickers?category=spot&symbol=BTCUSDT")["result"]["list"][0]["lastPrice"])
    d = get(f"https://api.bybit.com/v5/market/kline?category=spot&symbol=BTCUSDT&interval=60&limit={n + 1}")["result"]["list"]
    return p, [vela(*x[:5]) for x in reversed(d)][:-1]


def okx(n):
    p = float(get("https://www.okx.com/api/v5/market/ticker?instId=BTC-USDT")["data"][0]["last"])
    d = get(f"https://www.okx.com/api/v5/market/candles?instId=BTC-USDT&bar=1H&limit={n + 1}")["data"]
    return p, [vela(*x[:5]) for x in reversed(d) if x[8] == "1"]  # so velas confirmadas


def kraken(n):
    erro = None
    for par in ("XBTUSDT", "XBTUSD"):
        try:
            j = get(f"https://api.kraken.com/0/public/Ticker?pair={par}")
            if j.get("error"):
                raise RuntimeError(j["error"])
            p = float(list(j["result"].values())[0]["c"][0])
            j = get(f"https://api.kraken.com/0/public/OHLC?pair={par}&interval=60")
            d = [v for k, v in j["result"].items() if k != "last"][0][:-1]  # ultima em aberto
            return p, [vela(x[0] * 1000, *x[1:5]) for x in d][-n:]
        except Exception as e:
            erro = e
    raise erro


def coinbase(n):  # reserva (EUA-friendly), so usada se menos de 3 das 4 principais responderem
    p = float(get("https://api.exchange.coinbase.com/products/BTC-USD/ticker")["price"])
    d = get("https://api.exchange.coinbase.com/products/BTC-USD/candles?granularity=3600")  # [t, low, high, open, close, vol]
    agora_h = int(time.time()) // 3600 * 3600
    cs = [vela(x[0] * 1000, x[3], x[2], x[1], x[4]) for x in reversed(d) if x[0] < agora_h]
    return p, cs[-n:]


PRINCIPAIS = dict(binance=binance, bybit=bybit, okx=okx, kraken=kraken)


def consenso(n=N_VELAS):
    out, falhas = {}, {}
    for k, f in PRINCIPAIS.items():
        try:
            out[k] = f(n)
        except Exception as e:
            falhas[k] = f"{type(e).__name__}: {str(e)[:80]}"
    if len(out) < 3:
        try:
            out["coinbase"] = coinbase(n)
        except Exception as e:
            falhas["coinbase"] = f"{type(e).__name__}: {str(e)[:80]}"
    if not out:
        return None
    px = {k: v[0] for k, v in out.items()}
    med = st.median(px.values())
    por_t = {}
    for k, (_, cs) in out.items():
        for c in cs:
            por_t.setdefault(c["t"], {})[k] = c
    precisa = max(1, (len(out) + 1) // 2)  # vela com a maioria das fontes
    ts = sorted(t for t, v in por_t.items() if len(v) >= precisa)[-n:]
    velas = [dict(t=t, **{f: st.median(c[f] for c in por_t[t].values()) for f in "ohlc"}) for t in ts]
    return dict(mediana=med, precos=px, velas=velas, ultima_por_fonte=por_t.get(ts[-1], {}) if ts else {},
                falhas=falhas)


# ---------------- funding (porte do funding_oi.py) ----------------
def f_binance():
    f = float(get("https://fapi.binance.com/fapi/v1/premiumIndex?symbol=BTCUSDT")["lastFundingRate"])
    oi = float(get("https://fapi.binance.com/fapi/v1/openInterest?symbol=BTCUSDT")["openInterest"])
    return f, oi


def f_bybit():
    d = get("https://api.bybit.com/v5/market/tickers?category=linear&symbol=BTCUSDT")["result"]["list"][0]
    return float(d["fundingRate"]), float(d["openInterest"])


def f_okx():
    f = float(get("https://www.okx.com/api/v5/public/funding-rate?instId=BTC-USDT-SWAP")["data"][0]["fundingRate"])
    oi = float(get("https://www.okx.com/api/v5/public/open-interest?instType=SWAP&instId=BTC-USDT-SWAP")["data"][0]["oiCcy"])
    return f, oi


def f_deribit():  # reserva: funding_8h ja vem em base de 8h; OI em USD -> sem OI em BTC
    r = get("https://www.deribit.com/api/v2/public/ticker?instrument_name=BTC-PERPETUAL")["result"]
    return float(r["funding_8h"]), None


def f_hyperliquid():  # reserva: funding por hora -> x8 para ficar em base de 8h
    d = get("https://api.hyperliquid.xyz/info", dados={"type": "metaAndAssetCtxs"})
    i = [a["name"] for a in d[0]["universe"]].index("BTC")
    return float(d[1][i]["funding"]) * 8, float(d[1][i]["openInterest"])


def funding():
    r, falhas = {}, {}
    fontes = [("binance", f_binance), ("bybit", f_bybit), ("okx", f_okx)]
    for nome, fn in fontes + [("deribit", f_deribit), ("hyperliquid", f_hyperliquid)]:
        if nome in ("deribit", "hyperliquid") and len(r) >= 2:
            break  # reservas so entram se menos de 2 das 3 principais responderem
        try:
            f, oi = fn()
            r[nome] = {"funding_pct": round(f * 100, 4), "oi_btc": round(oi) if oi else None}
        except Exception as e:
            falhas[nome] = f"{type(e).__name__}: {str(e)[:80]}"
    return r, falhas


# ---------------- alertas ----------------
def dedup(state, alertas, tipo, valor, msg):
    s = state.setdefault("alertas", {})
    o, agora = s.get(tipo), int(time.time())
    if o and agora - o["t"] < ESPERA_H[tipo] * 3600 and abs(valor) < PIORA * abs(o["v"]):
        print(f"  (ja alertado: {msg})")
        return
    s[tipo] = {"t": agora, "v": valor}
    alertas.append(msg + (" (PIOROU)" if o and agora - o["t"] < ESPERA_H[tipo] * 3600 else ""))


def tr(c, ant):
    return max(c["h"] - c["l"], abs(c["h"] - ant["c"]), abs(c["l"] - ant["c"]))


def checar_volatilidade(c, state, alertas):
    v = c["velas"] if c else []
    if len(v) < 28:
        print(f"  ERRO: poucas velas 1h de consenso ({len(v)})")
        return
    ult = v[-1]
    hora = time.strftime("%d/%m %H:%M", time.localtime(ult["t"] / 1000))
    mov = (ult["c"] / ult["o"] - 1) * 100
    trs = [tr(v[i], v[i - 1]) for i in range(1, len(v))]
    atr3, atr24 = st.mean(trs[-3:]), st.mean(trs[-24:])
    ratio = atr3 / atr24
    print(f"  Vela 1h fechada {hora} (hora local): {mov:+.2f}% | ATR3 {atr3:,.0f} / media TR 24h {atr24:,.0f}"
          f" = {ratio:.2f} (limites {MOV_1H_PCT}% / {ATR_RATIO})")
    if abs(mov) >= MOV_1H_PCT and state.get("vela_mov1h") != ult["t"]:
        state["vela_mov1h"] = ult["t"]
        dedup(state, alertas, "mov1h", mov, f"BTC {mov:+.2f}% na vela de 1h das {hora} "
              f"(O {ult['o']:,.0f} -> C {ult['c']:,.0f})")
    if ratio >= ATR_RATIO:
        dedup(state, alertas, "atr", ratio, f"BTC volatilidade alta: ATR(3) 1h = {ratio:.2f}x a media de 24h "
              f"({atr3:,.0f} vs {atr24:,.0f} USD)")


def checar_desvio(c, state, alertas):
    if not c:
        print("  ERRO: nenhuma corretora respondeu")
        return
    med = c["mediana"]
    print(f"  Mediana US$ {med:,.2f} | " + " | ".join(f"{k} {p:,.1f} ({(p / med - 1) * 100:+.3f}%)"
                                                      for k, p in c["precos"].items()))
    if c["falhas"]:
        print("  Fontes com falha: " + "; ".join(f"{k}: {v}" for k, v in c["falhas"].items()))
    if len(c["precos"]) < 3:
        print("  (menos de 3 corretoras: desvio nao e confiavel, nao alerta)")
        return
    piores = []
    for k, p in c["precos"].items():
        d = (p / med - 1) * 100
        if abs(d) > DESVIO_PCT:
            piores.append((abs(d), f"{k} preco {d:+.3f}% vs mediana"))
    if c["velas"]:
        u = c["velas"][-1]
        for k, cv in c["ultima_por_fonte"].items():
            dh, dl = (cv["h"] / u["h"] - 1) * 100, (cv["l"] / u["l"] - 1) * 100
            if dh > DESVIO_PCT:
                piores.append((dh, f"{k} pavio superior 1h {dh:+.3f}%"))
            if dl < -DESVIO_PCT:
                piores.append((-dl, f"{k} pavio inferior 1h {dl:+.3f}%"))
    if piores:
        piores.sort(reverse=True)
        dedup(state, alertas, "desvio", piores[0][0], "DESVIO entre corretoras (> "
              f"{DESVIO_PCT}%): " + "; ".join(m for _, m in piores) + f". Mediana US$ {med:,.0f}")


def checar_funding(state, alertas):
    r, falhas = funding()
    if falhas:
        print("  Fontes com falha: " + "; ".join(f"{k}: {v}" for k, v in falhas.items()))
    if not r:
        print("  ERRO: nenhum funding disponivel")
        return
    fs = [v["funding_pct"] for v in r.values()]
    m, dv = st.mean(fs), max(fs) - min(fs)
    ois = [v["oi_btc"] for v in r.values() if v["oi_btc"]]
    print("  " + " | ".join(f"{k} {v['funding_pct']:+.4f}%" for k, v in r.items())
          + f" | media {m:+.4f}%/8h | divergencia {dv:.4f} p.p." + (f" | OI ~{sum(ois):,} BTC" if ois else ""))
    if abs(m) > FUNDING_EXTREMO:
        dedup(state, alertas, "funding", m, f"FUNDING medio extremo {m:+.4f}%/8h (limite ±{FUNDING_EXTREMO}%) "
              + str({k: v["funding_pct"] for k, v in r.items()}))
    if len(fs) >= 2 and dv > FUNDING_DIVERG:
        dedup(state, alertas, "diverg", dv, f"FUNDING divergente entre corretoras: {dv:.4f} p.p. "
              f"(limite {FUNDING_DIVERG}) " + str({k: v["funding_pct"] for k, v in r.items()}))


def checar_24h(c, state, alertas):
    chg, fonte = None, None
    try:
        d = get("https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd&include_24hr_change=true")
        chg, fonte = float(d["bitcoin"]["usd_24h_change"]), "CoinGecko"
    except Exception as e:
        print(f"  CoinGecko falhou ({str(e)[:60]}); usando velas de consenso")
        if c and len(c["velas"]) >= 25:
            chg, fonte = (c["mediana"] / c["velas"][-24]["o"] - 1) * 100, "consenso 1h"
    if chg is None:
        print("  ERRO: sem variacao 24h")
        return
    print(f"  BTC 24h {chg:+.2f}% ({fonte}) | limite ±{MOV_24H_PCT}%")
    if abs(chg) >= MOV_24H_PCT:
        lado = "sobe" if chg > 0 else "cai"
        dedup(state, alertas, "mov24h", chg, f"BTC {lado} {chg:+.2f}% em 24h ({fonte})")


def main():
    try:
        state = json.load(open(STATE))
    except Exception:
        state = {}
    antes = json.dumps(state, sort_keys=True)
    alertas = []

    c = consenso()
    print("== (1) Movimento 1h / pico de ATR ==")
    checar_volatilidade(c, state, alertas)
    print("== (2) Desvio de preco entre corretoras ==")
    checar_desvio(c, state, alertas)
    print("== (3) Funding ==")
    checar_funding(state, alertas)
    print("== (4) Movimento 24h ==")
    checar_24h(c, state, alertas)

    if json.dumps(state, sort_keys=True) != antes:
        with open(STATE, "w") as f:
            json.dump(state, f, indent=1, sort_keys=True)

    if alertas:
        texto = "₿ BTC ALERTA\n" + "\n".join(alertas)
        print("\n" + "!" * 60 + "\n" + texto + "\n" + "!" * 60)
        notificar(texto)
        sys.exit(1)  # falha de proposito -> GitHub manda e-mail
    print("\nNenhum alerta. Tudo calmo.")


if __name__ == "__main__":
    main()
