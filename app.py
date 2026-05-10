from flask import Flask, jsonify, render_template, request

from ia.analysis import (
    AISignalGenerator,
    BacktestEngine,
    OperationalScore,
    RiskManagement,
    TechnicalAnalysis,
    create_heatmap_data,
    generate_ai_reasoning,
)
from ia.binance_client import BinanceMarketData, TimedCache


app = Flask(__name__)

market = BinanceMarketData()
candle_cache = TimedCache(ttl_seconds=12)
ticker_cache = TimedCache(ttl_seconds=8)

SUPPORTED_TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h", "1d", "1w"]
HEATMAP_TIMEFRAMES = ["15m", "1h", "4h", "1d"]


def normalize_symbol(symbol):
    return symbol.replace("-", "").replace("/", "").upper()


def load_market_data(symbol, timeframe, limit=500):
    symbol = normalize_symbol(symbol)
    if timeframe not in SUPPORTED_TIMEFRAMES:
        timeframe = "1h"
    key = f"klines:{symbol}:{timeframe}:{limit}"
    cached = candle_cache.get(key)
    if cached is not None:
        return cached
    return candle_cache.set(key, market.get_klines(symbol, timeframe, limit))


def build_analysis(symbol, timeframe, limit=500):
    df = load_market_data(symbol, timeframe, limit)
    ta = TechnicalAnalysis(df)
    signal = AISignalGenerator(ta).generate_signal()
    atr = signal["indicators"]["atr"]
    levels = RiskManagement(df["close"].iloc[-1], atr).calculate_levels(signal["signal_type"])
    patterns = ta.identify_candle_patterns()
    score = OperationalScore.calculate_score(
        signal["indicators"],
        patterns,
        signal["components"]["trend_signal"]["value"],
        signal["score"],
    )
    return df, ta, signal, levels, patterns, score


def get_ticker(symbol):
    symbol = normalize_symbol(symbol)
    cached = ticker_cache.get(symbol)
    if cached:
        return cached
    return ticker_cache.set(symbol, market.get_24h_ticker(symbol))


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/advanced")
def advanced():
    return render_template("advanced.html")


@app.route("/api/candles/<symbol>/<timeframe>")
def get_candles(symbol, timeframe):
    try:
        limit = int(request.args.get("limit", 500))
        df = load_market_data(symbol, timeframe, limit)
        ta = TechnicalAnalysis(df)
        payload = ta.chart_payload()
        ticker = get_ticker(symbol)
        return jsonify({
            "success": True,
            "source": "binance",
            "symbol": normalize_symbol(symbol),
            "timeframe": timeframe,
            "candles": payload["candles"],
            "volumes": payload["volumes"],
            "overlays": payload["overlays"],
            "ticker": {
                "lastPrice": float(ticker.get("lastPrice", df["close"].iloc[-1])),
                "priceChangePercent": float(ticker.get("priceChangePercent", 0)),
                "quoteVolume": float(ticker.get("quoteVolume", 0)),
                "volume": float(ticker.get("volume", 0)),
                "count": int(ticker.get("count", 0)),
            },
        })
    except Exception as error:
        return jsonify({"success": False, "error": str(error)}), 400


@app.route("/api/analysis/<symbol>/<timeframe>")
def get_analysis(symbol, timeframe):
    try:
        df, ta, signal, levels, patterns, score = build_analysis(symbol, timeframe)
        ticker = get_ticker(symbol)
        current_price = float(df["close"].iloc[-1])
        previous_price = float(df["close"].iloc[-2]) if len(df) > 1 else current_price
        price_change = ((current_price - previous_price) / previous_price * 100) if previous_price else 0
        markers = build_trade_markers(df, signal, levels)

        return jsonify({
            "success": True,
            "source": "binance",
            "symbol": normalize_symbol(symbol),
            "timeframe": timeframe,
            "signal": signal,
            "levels": levels,
            "markers": markers,
            "support_resistance": ta.identify_support_resistance(lookback=20, num_levels=4),
            "patterns": patterns,
            "candle_reading": ta.read_latest_candles(5),
            "reasoning": generate_ai_reasoning(signal),
            "operational_score": score,
            "current_price": current_price,
            "price_change": price_change,
            "ticker": {
                "priceChangePercent": float(ticker.get("priceChangePercent", 0)),
                "quoteVolume": float(ticker.get("quoteVolume", 0)),
                "volume": float(ticker.get("volume", 0)),
                "count": int(ticker.get("count", 0)),
            },
        })
    except Exception as error:
        return jsonify({"success": False, "error": str(error)}), 400


def build_trade_markers(df, signal, levels):
    last_time = int(df.index[-1].timestamp())
    is_buy = any(word in signal["signal_type"] for word in ["compra", "entrada"])
    signal_text = {
        "entrada_agressiva": "Entrada agressiva",
        "entrada_conservadora": "Entrada conservadora",
        "compra": "Compra",
        "venda": "Venda",
        "venda_agressiva": "Venda agressiva",
        "neutro": "Neutro",
    }.get(signal["signal_type"], signal["signal_type"])
    return [{
        "time": last_time,
        "position": "belowBar" if is_buy else "aboveBar",
        "color": "#10b981" if is_buy else "#ef4444",
        "shape": "arrowUp" if is_buy else "arrowDown",
        "text": signal_text,
        "levels": levels,
    }]


@app.route("/api/multi-timeframe/<symbol>")
def get_multi_timeframe(symbol):
    try:
        analysis = {}
        bullish = 0
        bearish = 0
        for timeframe in HEATMAP_TIMEFRAMES:
            _, _, signal, _, _, score = build_analysis(symbol, timeframe, limit=350)
            signal_type = signal["signal_type"]
            if any(word in signal_type for word in ["compra", "entrada"]):
                bullish += 1
            elif "venda" in signal_type:
                bearish += 1
            analysis[timeframe] = {
                "signal": signal_type,
                "confidence": signal["confidence"],
                "score": signal["score"],
                "operational_score": score,
            }

        consolidated = "NEUTRO"
        if bullish > bearish:
            consolidated = "COMPRA_CONFIRMADA"
        elif bearish > bullish:
            consolidated = "VENDA_CONFIRMADA"

        return jsonify({
            "success": True,
            "symbol": normalize_symbol(symbol),
            "analysis": analysis,
            "consolidated_signal": consolidated,
            "alignment": f"{max(bullish, bearish)}/{len(HEATMAP_TIMEFRAMES)}",
        })
    except Exception as error:
        return jsonify({"success": False, "error": str(error)}), 400


@app.route("/api/heatmap")
def get_heatmap():
    try:
        assets = market.get_assets()
        selected = request.args.get("symbols")
        if selected:
            requested = [normalize_symbol(item) for item in selected.split(",") if item.strip()]
            assets = [asset for asset in assets if asset["symbol"] in requested] or assets

        heatmap = create_heatmap_data(
            assets,
            HEATMAP_TIMEFRAMES,
            data_loader=lambda symbol, timeframe: load_market_data(symbol, timeframe, 300),
        )
        return jsonify({"success": True, "source": "binance", "heatmap": heatmap})
    except Exception as error:
        return jsonify({"success": False, "error": str(error)}), 400


@app.route("/api/backtest/<symbol>")
def run_backtest(symbol):
    try:
        df = load_market_data(symbol, "1d", 500)
        result = BacktestEngine(df, initial_capital=10000).backtest_strategy("ema_cross")
        return jsonify({"success": True, "source": "binance", "symbol": normalize_symbol(symbol), "backtest": result})
    except Exception as error:
        return jsonify({"success": False, "error": str(error)}), 400


@app.route("/api/assets")
def get_assets():
    return jsonify({"success": True, "source": "binance", "assets": market.get_assets()})


@app.route("/api/timeframes")
def get_timeframes():
    return jsonify({"success": True, "timeframes": SUPPORTED_TIMEFRAMES})


if __name__ == "__main__":
    app.run(debug=True)
