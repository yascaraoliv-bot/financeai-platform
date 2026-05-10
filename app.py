import os
import math

import requests
from flask import Flask, jsonify, redirect, render_template, request, session, url_for

from ia.analysis import (
    AISignalGenerator,
    BacktestEngine,
    OperationalScore,
    RiskManagement,
    TechnicalAnalysis,
    create_heatmap_data,
    generate_ai_reasoning,
)
from ia.binance_client import TimedCache
from ia.confluence_engine import DISCLAIMER, build_confluence_analysis
from ia.data_generator import generate_realistic_data
from ia.final_score import calculate_final_score
from ia.institutional import OperationalValidator, PatternLearner, ProfessionalBacktest
from ia.live_trading import build_live_status
from ia.live_signals import LiveSignalManager
from ia.market_data_router import MarketDataRouter
from ia.operational_signal import build_operational_signal
from ia.operacional_reader import build_candle_flow, build_operacional_context, build_operacional_reading
from ia.replay_engine import ReplayEngine
from ia.smart_money import analyze_smart_money
from ia.technical_reader import read_technical
from ia.volume_reader import read_volume
from ia.wyckoff import read_wyckoff
from ia.user_store import (
    add_watchlist,
    authenticate,
    create_alert,
    create_user,
    current_user_id,
    get_setting,
    get_watchlist,
    init_db,
    list_alerts,
    login_required,
    remove_watchlist,
    save_setting,
)


app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-secret-change-me")
init_db()

market = MarketDataRouter()
candle_cache = TimedCache(ttl_seconds=12)
ticker_cache = TimedCache(ttl_seconds=8)
chart_payload_cache = TimedCache(ttl_seconds=8)
analysis_response_cache = TimedCache(ttl_seconds=10)
analysis_core_cache = TimedCache(ttl_seconds=10)
live_status_cache = TimedCache(ttl_seconds=3)
live_signal_manager = LiveSignalManager()
replay_engine = ReplayEngine()

SUPPORTED_TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h", "1d", "1w"]
HEATMAP_TIMEFRAMES = ["5m", "15m", "1h", "4h", "1d"]
DEFAULT_SYMBOL = "BTCUSDT"


def normalize_symbol(symbol):
    value = (symbol or DEFAULT_SYMBOL).replace("-", "").replace("/", "").upper()
    if not value:
        return DEFAULT_SYMBOL
    return value


def normalize_timeframe(timeframe):
    return timeframe if timeframe in SUPPORTED_TIMEFRAMES else "1h"


def sanitize_json(value):
    if isinstance(value, dict):
        return {key: sanitize_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_json(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_json(item) for item in value]
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return value


def default_signal(df):
    price = float(df["close"].iloc[-1]) if df is not None and len(df) else 0.0
    return {
        "signal_type": "neutro",
        "score": 0.0,
        "confidence": 0.0,
        "components": {"trend_signal": {"value": "sideways", "strength": 0}},
        "timestamp": None,
        "price": price,
        "indicators": {
            "rsi": 50.0,
            "macd": 0.0,
            "signal": 0.0,
            "histogram": 0.0,
            "ema9": price,
            "ema21": price,
            "ema200": price,
            "bollinger_upper": price,
            "bollinger_middle": price,
            "bollinger_lower": price,
            "atr": max(price * 0.01, 0.0001),
            "vwap": price,
            "volume": 0.0,
            "volume_sma": 0.0,
        },
    }


def default_smc():
    return {
        "smc_score": 50,
        "institutional_bias": "neutral",
        "has_bos": False,
        "bos": "none",
        "has_choch": False,
        "choch": "none",
        "liquidity_zone": None,
        "nearest_order_block": None,
        "relevant_order_block": None,
        "relevant_fvg": None,
        "liquidity_sweep": {"detected": False, "side": "none", "zone": None},
        "false_breakout": {"detected": False, "direction": "none", "level": None},
        "inducement": {"detected": False, "side": "none", "zone": None},
        "institutional_zone": None,
        "institutional_zones": [],
        "confirmed": False,
        "invalidated": False,
        "score_adjustment": 0,
        "reasons": [],
        "confirmations": [],
        "invalidations": [],
        "explanation": "Smart Money sem leitura suficiente.",
        "structure": {"trend": "neutral", "bos": "none", "choch": "none"},
        "order_blocks": [],
        "liquidity": [],
        "fair_value_gaps": [],
    }


def default_volume():
    return {
        "volume_above_average": False,
        "buyer_volume": 0,
        "seller_volume": 0,
        "dominant_side": "BALANCED",
        "abnormal_volume": False,
        "exhaustion": {"detected": False, "side": "NONE"},
        "absorption": {"detected": False, "side": "NONE"},
        "breakout_confirmation": {"confirmed": False, "direction": "NONE", "level": None},
        "price_volume_divergence": {"detected": False, "type": "NONE"},
        "score_adjustment": 0,
        "signal": "NEUTRAL_VOLUME",
        "confidence": 0,
        "reasons": [],
        "metrics": {},
    }


def default_validation():
    return {
        "entry_quality": {
            "quality": "neutra",
            "probability": 0,
            "invalidated": False,
            "aligned_with_smc": False,
        },
        "false_breakout": {"detected": False, "side": "none", "level": None},
        "pullback": {"detected": False, "side": "none", "strength": 0},
        "lateralization": {"detected": False, "range_pct": 0, "atr_pct": 0},
    }


def default_technical(df):
    price = float(df["close"].iloc[-1]) if df is not None and len(df) else 0
    return {
        "signal": "NEUTRAL",
        "score": 0,
        "confidence": 0,
        "trend": {"direction": "SIDEWAYS", "price_above_ema200": False, "ema_stack": {}},
        "confirmations": [],
        "invalidations": ["Analise tecnica indisponivel."],
        "entry_price": price,
        "stop_loss": price,
        "take_profit_1": price,
        "take_profit_2": price,
        "take_profit_3": price,
        "explanation": "Cenario neutro / sem entrada no momento.",
        "details": {},
    }


def default_final_score(levels=None):
    levels = levels or {}
    return {
        "score": 0,
        "confidence": 0,
        "signal": "NEUTRAL",
        "classification": "Nao operar",
        "entry_aggressive": False,
        "entry_conservative": False,
        "stop_loss": levels.get("stop_loss"),
        "take_profit_1": levels.get("alvo_1"),
        "take_profit_2": levels.get("alvo_2"),
        "take_profit_3": levels.get("alvo_2"),
        "technical_reasons": [],
        "invalidation_reasons": ["Score indisponivel; grafico mantido em modo neutro."],
        "components": {},
        "explanation": "Cenario neutro / sem entrada no momento.",
    }


def default_wyckoff():
    return {
        "phase": "indefinida",
        "wyckoff_phase": "indefinida",
        "probable_market_phase": "indefinida",
        "bias": "neutral",
        "accumulation": False,
        "distribution": False,
        "spring": False,
        "upthrust": False,
        "climax": False,
        "selling_climax": False,
        "buying_climax": False,
        "test": False,
        "range": {},
        "volume_ratio": 0,
        "score_adjustment": 0,
        "confirmations": [],
        "invalidations": [],
        "explanation": "Wyckoff sem leitura suficiente.",
    }


def build_institutional_payload(smc, wyckoff):
    smc = smc or default_smc()
    wyckoff = wyckoff or default_wyckoff()
    confirmations = list(smc.get("confirmations") or [])
    confirmations.extend(wyckoff.get("confirmations") or [])
    invalidations = list(smc.get("invalidations") or [])
    invalidations.extend(wyckoff.get("invalidations") or [])
    explanation_parts = [text for text in [smc.get("explanation"), wyckoff.get("explanation")] if text]
    return {
        "smc_score": smc.get("smc_score", 50),
        "wyckoff_phase": wyckoff.get("wyckoff_phase") or wyckoff.get("phase", "indefinida"),
        "institutional_bias": smc.get("institutional_bias", wyckoff.get("bias", "neutral")),
        "relevant_order_block": smc.get("relevant_order_block") or smc.get("nearest_order_block"),
        "relevant_fvg": smc.get("relevant_fvg"),
        "liquidity_zone": smc.get("liquidity_zone"),
        "liquidity_sweep": smc.get("liquidity_sweep", {"detected": False, "side": "none", "zone": None}),
        "false_breakout": smc.get("false_breakout", {"detected": False, "direction": "none", "level": None}),
        "confirmations": confirmations[:12],
        "invalidations": invalidations[:12],
        "explanation": " ".join(explanation_parts) or "Sem contexto institucional dominante.",
    }


def load_market_data(symbol, timeframe, limit=500):
    symbol = normalize_symbol(symbol)
    timeframe = normalize_timeframe(timeframe)
    limit = max(60, min(int(limit or 500), 1000))
    key = f"klines:{symbol}:{timeframe}:{limit}"
    cached = candle_cache.get(key)
    if cached is not None:
        return cached
    try:
        return candle_cache.set(key, market.get_klines(symbol, timeframe, limit))
    except Exception:
        if market.identify_market(symbol) == "crypto":
            if symbol != DEFAULT_SYMBOL:
                fallback_key = f"klines:{DEFAULT_SYMBOL}:{timeframe}:{limit}"
                cached = candle_cache.get(fallback_key)
                if cached is not None:
                    return cached
                return candle_cache.set(fallback_key, market.get_klines(DEFAULT_SYMBOL, timeframe, limit))
            df = generate_realistic_data(DEFAULT_SYMBOL, days=90, interval=timeframe)
            return candle_cache.set(key, df.tail(limit))
        raise


def get_cached_chart_payload(symbol, timeframe, limit):
    symbol = normalize_symbol(symbol)
    timeframe = normalize_timeframe(timeframe)
    key = f"chart:{symbol}:{timeframe}:{limit}"
    cached = chart_payload_cache.get(key)
    if cached is not None:
        return cached
    df = load_market_data(symbol, timeframe, limit)
    payload = TechnicalAnalysis(df).chart_payload()
    result = {"df": df, "payload": payload}
    return chart_payload_cache.set(key, result)


def build_analysis(symbol, timeframe, limit=500):
    symbol = normalize_symbol(symbol)
    timeframe = normalize_timeframe(timeframe)
    limit = max(60, min(int(limit or 500), 1000))
    cache_key = f"analysis-core:{symbol}:{timeframe}:{limit}"
    cached = analysis_core_cache.get(cache_key)
    if cached is not None:
        return cached

    df = load_market_data(symbol, timeframe, limit)
    ta = TechnicalAnalysis(df)
    try:
        signal = AISignalGenerator(ta).generate_signal()
    except Exception:
        signal = default_signal(df)
    atr = signal["indicators"]["atr"]
    try:
        levels = RiskManagement(df["close"].iloc[-1], atr).calculate_levels(signal["signal_type"])
    except Exception:
        levels = RiskManagement(float(df["close"].iloc[-1]), max(float(df["close"].iloc[-1]) * 0.01, 0.0001)).calculate_levels("neutro")
    try:
        patterns = ta.identify_candle_patterns()
    except Exception:
        patterns = []
    try:
        score = OperationalScore.calculate_score(
            signal["indicators"],
            patterns,
            signal["components"]["trend_signal"]["value"],
            signal["score"],
        )
    except Exception:
        score = 50
    try:
        smc = analyze_smart_money(df, signal["signal_type"])
    except Exception:
        smc = default_smc()
    score = int(max(0, min(100, score + smc.get("score_adjustment", 0))))
    try:
        volume_analysis = read_volume(df)
    except Exception:
        volume_analysis = default_volume()
    try:
        wyckoff = read_wyckoff(df)
    except Exception:
        wyckoff = default_wyckoff()
    score = int(max(0, min(100, score + volume_analysis.get("score_adjustment", 0) + wyckoff.get("score_adjustment", 0))))
    try:
        validation = OperationalValidator(df, signal, levels, smc).validate()
    except Exception:
        validation = default_validation()
    if smc.get("invalidated"):
        validation["entry_quality"]["invalidated"] = True
        validation["entry_quality"]["quality"] = "ruim"
        validation["entry_quality"]["probability"] = min(validation["entry_quality"]["probability"], 42)
    elif smc.get("confirmed"):
        validation["entry_quality"]["probability"] = min(95, validation["entry_quality"]["probability"] + 8)
    result = (df, ta, signal, levels, patterns, score, smc, validation, volume_analysis, wyckoff)
    return analysis_core_cache.set(cache_key, result)


def direction_from_signal(signal_type, technical_signal=None):
    signal_text = (technical_signal or signal_type or "").lower()
    if any(word in signal_text for word in ["buy", "compra", "entrada"]):
        return "BULLISH"
    if any(word in signal_text for word in ["sell", "venda"]):
        return "BEARISH"
    return "NEUTRAL"


def direction_from_trend(trend):
    direction = trend.get("direction", "SIDEWAYS")
    if "BULLISH" in direction:
        return "BULLISH"
    if "BEARISH" in direction:
        return "BEARISH"
    return "NEUTRAL"


def build_multi_timeframe(symbol, timeframes=None):
    timeframes = timeframes or HEATMAP_TIMEFRAMES
    analysis = {}
    counts = {"BULLISH": 0, "BEARISH": 0, "NEUTRAL": 0}
    strengths = {"BULLISH": [], "BEARISH": [], "NEUTRAL": []}

    for timeframe in timeframes:
        try:
            df, _, signal, _, _, score, _, validation, volume_analysis, _ = build_analysis(symbol, timeframe, limit=500)
        except Exception:
            df = load_market_data(DEFAULT_SYMBOL, timeframe, 500)
            signal = default_signal(df)
            score = 50
            validation = default_validation()
            volume_analysis = default_volume()
        try:
            technical = read_technical(df)
        except Exception:
            technical = {
                "signal": "NEUTRAL",
                "score": 0,
                "confidence": 0,
                "trend": {"direction": "SIDEWAYS"},
            }
        signal_direction = direction_from_signal(signal["signal_type"], technical["signal"])
        trend_direction = direction_from_trend(technical["trend"])
        direction = signal_direction if signal_direction != "NEUTRAL" else trend_direction
        strength = int(max(0, min(100, round((score * 0.55) + (technical["confidence"] * 0.35) + (abs(technical["score"]) * 3)))))
        counts[direction] += 1
        strengths[direction].append(strength)
        analysis[timeframe] = {
            "trend": technical["trend"]["direction"],
            "trend_direction": trend_direction,
            "signal": signal["signal_type"],
            "technical_signal": technical["signal"],
            "direction": direction,
            "strength": strength,
            "confidence": signal["confidence"],
            "score": signal["score"],
            "operational_score": score,
            "probability": validation["entry_quality"]["probability"],
            "volume_signal": volume_analysis["signal"],
        }

    dominant_direction = max(counts, key=lambda item: counts[item])
    dominant_count = counts[dominant_direction]
    strong_signal_allowed = dominant_direction != "NEUTRAL" and dominant_count >= 3
    average_strength = int(round(sum(strengths[dominant_direction]) / len(strengths[dominant_direction]))) if strengths[dominant_direction] else 0
    confluence = {
        "dominant_direction": dominant_direction,
        "confirmed_timeframes": dominant_count,
        "required_confirmations": 3,
        "strong_signal_allowed": strong_signal_allowed,
        "average_strength": average_strength,
        "counts": counts,
    }
    return analysis, confluence


def apply_mtf_gate(signal, score, validation, confluence):
    gated_signal = dict(signal)
    if confluence["strong_signal_allowed"]:
        if confluence["dominant_direction"] == "BULLISH" and any(word in signal["signal_type"] for word in ["compra", "entrada"]):
            gated_signal["signal_type"] = "entrada_agressiva" if score >= 70 else "entrada_conservadora"
        elif confluence["dominant_direction"] == "BEARISH" and "venda" in signal["signal_type"]:
            gated_signal["signal_type"] = "venda_agressiva" if score >= 70 else "venda"
        gated_signal["mtf_confirmed"] = True
    else:
        if signal["signal_type"] in ["entrada_agressiva", "entrada_conservadora", "venda_agressiva"]:
            gated_signal["signal_type"] = "compra" if any(word in signal["signal_type"] for word in ["compra", "entrada"]) else "venda"
        gated_signal["mtf_confirmed"] = False
        validation["entry_quality"]["probability"] = min(validation["entry_quality"]["probability"], 58)
    return gated_signal, validation


def build_operational_state(signal, validation, smc, volume_analysis, final_score, mtf_confluence, levels):
    signal_type = final_score.get("signal", "NEUTRAL")
    invalidation_reasons = []
    strong_invalidators = {
        "false_breakout": False,
        "support_loss": False,
        "choch_against_signal": False,
        "opposite_volume": False,
        "bad_risk_reward": False,
    }

    false_breakout = smc.get("false_breakout", {})
    if false_breakout.get("detected"):
        strong_invalidators["false_breakout"] = True
        invalidation_reasons.append("Falso rompimento detectado.")

    structure = smc.get("structure", {})
    choch = structure.get("choch", "none")
    if (signal_type == "BUY" and choch == "bearish") or (signal_type == "SELL" and choch == "bullish"):
        strong_invalidators["choch_against_signal"] = True
        invalidation_reasons.append("CHOCH contra o sinal atual.")

    dominant_volume = volume_analysis.get("dominant_side")
    if (signal_type == "BUY" and dominant_volume == "SELLER") or (signal_type == "SELL" and dominant_volume == "BUYER"):
        strong_invalidators["opposite_volume"] = True
        invalidation_reasons.append("Volume dominante contra o sinal.")

    rr = float(levels.get("risco_retorno", 0))
    if rr < 1:
        strong_invalidators["bad_risk_reward"] = True
        invalidation_reasons.append(f"Risco/retorno ruim: 1:{rr:.2f}.")

    technical_invalidations = final_score.get("invalidation_reasons", [])
    if any("suporte" in reason.lower() and "perda" in reason.lower() for reason in technical_invalidations):
        strong_invalidators["support_loss"] = True
        invalidation_reasons.append("Perda de suporte relevante.")

    has_strong_invalidation = any(strong_invalidators.values())
    confluence_ok = mtf_confluence.get("strong_signal_allowed", False)
    score = float(final_score.get("score", 0))

    if has_strong_invalidation:
        state = "invalidated"
        message = "Cenario invalidado por fator tecnico forte."
    elif signal_type == "NEUTRAL" or signal.get("signal_type") == "neutro":
        state = "neutral"
        message = "Cenario neutro / sem entrada no momento."
    elif not confluence_ok or score < 7:
        state = "waiting_confirmation"
        message = "Aguardar confirmacao."
    else:
        state = "confirmed"
        message = "Sinal confirmado."

    return {
        "state": state,
        "message": message,
        "strong_invalidators": strong_invalidators,
        "invalidation_reasons": invalidation_reasons,
        "confluence_ok": confluence_ok,
        "ready": True,
    }


def get_ticker(symbol):
    symbol = normalize_symbol(symbol)
    cached = ticker_cache.get(symbol)
    if cached:
        return cached
    try:
        return ticker_cache.set(symbol, market.get_24h_ticker(symbol))
    except Exception:
        if symbol != DEFAULT_SYMBOL and market.identify_market(symbol) == "crypto":
            return get_ticker(DEFAULT_SYMBOL)
        meta = market.last_meta(symbol)
        return {"lastPrice": 0, "priceChangePercent": 0, "quoteVolume": 0, "volume": 0, "count": 0, **meta}


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/advanced")
@login_required
def advanced():
    return render_template("advanced.html")


@app.route("/live")
@login_required
def live():
    return render_template("live.html")


@app.route("/replay")
@login_required
def replay():
    return render_template("replay.html")


@app.route("/operacional")
@login_required
def operacional():
    return render_template("operacional.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        user = authenticate(username, password)
        if user:
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            return redirect(url_for("advanced"))
        return render_template("login.html", error="Usuario ou senha invalidos")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/api/register", methods=["POST"])
def api_register():
    payload = request.get_json(silent=True) or {}
    try:
        create_user(payload["username"], payload["password"])
        return jsonify({"success": True})
    except Exception as error:
        return jsonify({"success": False, "error": str(error)}), 400


@app.route("/api/candles/<symbol>/<timeframe>")
def get_candles(symbol, timeframe):
    requested_symbol = normalize_symbol(symbol)
    timeframe = normalize_timeframe(timeframe)
    try:
        limit = int(request.args.get("limit", 200))
        cached_payload = get_cached_chart_payload(requested_symbol, timeframe, limit)
        df = cached_payload["df"]
        payload = cached_payload["payload"]
        ticker = get_ticker(requested_symbol)
        market_meta = market.last_meta(requested_symbol)
        response = {
            "success": True,
            "source": market_meta.get("source", "binance"),
            "requested_symbol": requested_symbol,
            "symbol": requested_symbol if requested_symbol == DEFAULT_SYMBOL else normalize_symbol(symbol),
            "market": market_meta.get("market"),
            "market_label": market_meta.get("market_label"),
            "market_status": market_meta.get("market_status", "unknown"),
            "market_message": market_meta.get("message"),
            "streaming": market_meta.get("streaming", False),
            "timeframe": timeframe,
            "candles": payload.get("candles", []),
            "volumes": payload.get("volumes", []),
            "overlays": payload.get("overlays", {}),
            "ticker": {
                "lastPrice": float(ticker.get("lastPrice", df["close"].iloc[-1])),
                "priceChangePercent": float(ticker.get("priceChangePercent", 0)),
                "quoteVolume": float(ticker.get("quoteVolume", 0)),
                "volume": float(ticker.get("volume", 0)),
                "count": int(ticker.get("count", 0)),
            },
        }
        return jsonify(sanitize_json(response))
    except Exception as error:
        if market.identify_market(requested_symbol) != "crypto":
            meta = market.last_meta(requested_symbol)
            return jsonify(sanitize_json({
                "success": False,
                "source": meta.get("source"),
                "requested_symbol": requested_symbol,
                "symbol": requested_symbol,
                "market": meta.get("market"),
                "market_label": meta.get("market_label"),
                "market_status": "no_data",
                "market_message": meta.get("message") or f"Nao ha dados disponiveis para {requested_symbol} agora.",
                "streaming": False,
                "timeframe": timeframe,
                "candles": [],
                "volumes": [],
                "overlays": {},
                "error": str(error),
                "ticker": {"lastPrice": 0, "priceChangePercent": 0, "quoteVolume": 0, "volume": 0, "count": 0},
            })), 200
        try:
            df = load_market_data(DEFAULT_SYMBOL, "1h", 500)
            payload = TechnicalAnalysis(df).chart_payload()
            return jsonify(sanitize_json({
                "success": True,
                "source": "fallback",
                "requested_symbol": requested_symbol,
                "symbol": DEFAULT_SYMBOL,
                "market": "crypto",
                "market_label": "Criptomoedas",
                "market_status": "fallback",
                "market_message": f"Nao foi possivel carregar {requested_symbol}. Exibindo BTCUSDT como fallback.",
                "streaming": True,
                "timeframe": "1h",
                "candles": payload.get("candles", []),
                "volumes": payload.get("volumes", []),
                "overlays": payload.get("overlays", {}),
                "warning": str(error),
                "ticker": {"lastPrice": float(df["close"].iloc[-1]), "priceChangePercent": 0, "quoteVolume": 0, "volume": 0, "count": 0},
            }))
        except Exception as fallback_error:
            return jsonify({"success": False, "error": str(fallback_error), "candles": [], "volumes": [], "overlays": {}}), 200


@app.route("/api/analysis/<symbol>/<timeframe>")
def get_analysis(symbol, timeframe):
    symbol = normalize_symbol(symbol)
    timeframe = normalize_timeframe(timeframe)
    cache_key = f"analysis:{symbol}:{timeframe}"
    cached = analysis_response_cache.get(cache_key)
    if cached is not None:
        return jsonify(sanitize_json(cached))
    try:
        df, ta, signal, levels, patterns, score, smc, validation, volume_analysis, wyckoff = build_analysis(symbol, timeframe)
        ticker = get_ticker(symbol)
        market_meta = market.last_meta(symbol)
        current_price = float(df["close"].iloc[-1])
        previous_price = float(df["close"].iloc[-2]) if len(df) > 1 else current_price
        price_change = ((current_price - previous_price) / previous_price * 100) if previous_price else 0
        try:
            mtf_analysis, mtf_confluence = build_multi_timeframe(symbol)
        except Exception:
            mtf_analysis, mtf_confluence = {}, {
                "dominant_direction": "NEUTRAL",
                "confirmed_timeframes": 0,
                "required_confirmations": 3,
                "strong_signal_allowed": False,
                "average_strength": 0,
                "counts": {"BULLISH": 0, "BEARISH": 0, "NEUTRAL": 0},
            }
        try:
            signal, validation = apply_mtf_gate(signal, score, validation, mtf_confluence)
        except Exception:
            signal = default_signal(df)
            validation = default_validation()
        markers = build_trade_markers(df, signal, levels)
        try:
            technical_reading = read_technical(df)
        except Exception:
            technical_reading = default_technical(df)
        try:
            final_score = calculate_final_score(
                technical=technical_reading,
                ai_signal=signal,
                levels=levels,
                smc=smc,
                volume=volume_analysis,
                mtf_analysis=mtf_analysis,
                mtf_confluence=mtf_confluence,
                wyckoff=wyckoff,
            )
        except Exception:
            final_score = default_final_score(levels)
        score = int(round(final_score["score"] * 10))
        try:
            operational_state = build_operational_state(signal, validation, smc, volume_analysis, final_score, mtf_confluence, levels)
        except Exception:
            operational_state = {
                "state": "neutral",
                "message": "Cenario neutro / sem entrada no momento.",
                "strong_invalidators": {},
                "invalidation_reasons": [],
                "confluence_ok": False,
                "ready": True,
            }
        confluence_ai = build_confluence_analysis(
            technical=technical_reading,
            smc=smc,
            volume=volume_analysis,
            wyckoff=wyckoff,
            mtf={"analysis": mtf_analysis, "confluence": mtf_confluence},
            levels=levels,
            final_score=final_score,
        )
        operational_signal = build_operational_signal(
            confluence_ai=confluence_ai,
            technical=technical_reading,
            volume=volume_analysis,
            smc=smc,
            mtf_confluence=mtf_confluence,
            levels=levels,
            operational_state=operational_state,
        )
        institutional_payload = build_institutional_payload(smc, wyckoff)

        response = {
            "success": True,
            "source": market_meta.get("source", "binance"),
            "symbol": normalize_symbol(symbol),
            "market": market_meta.get("market"),
            "market_label": market_meta.get("market_label"),
            "market_status": market_meta.get("market_status", "unknown"),
            "market_message": market_meta.get("message"),
            "streaming": market_meta.get("streaming", False),
            "timeframe": timeframe,
            "signal": signal,
            "levels": levels,
            "markers": markers,
            "support_resistance": ta.identify_support_resistance(lookback=20, num_levels=4),
            "patterns": patterns,
            "smc": smc,
            "volume_analysis": volume_analysis,
            "wyckoff": wyckoff,
            "institutional_context": institutional_payload,
            "smc_score": institutional_payload["smc_score"],
            "wyckoff_phase": institutional_payload["wyckoff_phase"],
            "institutional_bias": institutional_payload["institutional_bias"],
            "relevant_order_block": institutional_payload["relevant_order_block"],
            "relevant_fvg": institutional_payload["relevant_fvg"],
            "liquidity_zone": institutional_payload["liquidity_zone"],
            "liquidity_sweep": institutional_payload["liquidity_sweep"],
            "false_breakout": institutional_payload["false_breakout"],
            "confirmations": institutional_payload["confirmations"],
            "invalidations": institutional_payload["invalidations"],
            "explanation": institutional_payload["explanation"],
            "multi_timeframe": {
                "analysis": mtf_analysis,
                "confluence": mtf_confluence,
            },
            "validation": validation,
            "scenario": build_scenario(signal, validation, smc),
            "technical_reader": technical_reading,
            "final_score": final_score,
            "confluence_ai": confluence_ai,
            "operational_signal": operational_signal,
            "disclaimer": DISCLAIMER,
            "operational_state": operational_state,
            "candle_reading": ta.read_latest_candles(5),
            "reasoning": final_score["technical_reasons"] + final_score["invalidation_reasons"],
            "operational_score": score,
            "current_price": current_price,
            "price_change": price_change,
            "ticker": {
                "priceChangePercent": float(ticker.get("priceChangePercent", 0)),
                "quoteVolume": float(ticker.get("quoteVolume", 0)),
                "volume": float(ticker.get("volume", 0)),
                "count": int(ticker.get("count", 0)),
            },
        }
        analysis_response_cache.set(cache_key, response)
        return jsonify(sanitize_json(response))
    except Exception as error:
        institutional_payload = build_institutional_payload(default_smc(), default_wyckoff())
        market_meta = market.last_meta(symbol)
        return jsonify(sanitize_json({
            "success": False,
            "error": str(error),
            "symbol": normalize_symbol(symbol),
            "source": market_meta.get("source"),
            "market": market_meta.get("market"),
            "market_label": market_meta.get("market_label"),
            "market_status": "no_data",
            "market_message": market_meta.get("message") or f"Nao ha dados suficientes para analisar {normalize_symbol(symbol)} agora.",
            "streaming": market_meta.get("streaming", False),
            "signal": default_signal(None),
            "levels": {},
            "markers": [],
            "smc": default_smc(),
            "volume_analysis": default_volume(),
            "wyckoff": default_wyckoff(),
            "institutional_context": institutional_payload,
            "smc_score": institutional_payload["smc_score"],
            "wyckoff_phase": institutional_payload["wyckoff_phase"],
            "institutional_bias": institutional_payload["institutional_bias"],
            "relevant_order_block": institutional_payload["relevant_order_block"],
            "relevant_fvg": institutional_payload["relevant_fvg"],
            "liquidity_zone": institutional_payload["liquidity_zone"],
            "liquidity_sweep": institutional_payload["liquidity_sweep"],
            "false_breakout": institutional_payload["false_breakout"],
            "confirmations": institutional_payload["confirmations"],
            "invalidations": institutional_payload["invalidations"],
            "explanation": institutional_payload["explanation"],
            "validation": default_validation(),
            "technical_reader": default_technical(None),
            "final_score": default_final_score({}),
            "operational_signal": {},
            "confluence_ai": build_confluence_analysis(default_technical(None), default_smc(), default_volume(), default_wyckoff(), {}, {}, default_final_score({})),
            "disclaimer": DISCLAIMER,
            "operational_state": {
                "state": "neutral",
                "message": "Cenario neutro / sem entrada no momento.",
                "ready": False,
            },
            "operational_score": 0,
        })), 200


def build_scenario(signal, validation, smc):
    quality = validation["entry_quality"]
    invalidated = quality["invalidated"]
    signal_type = signal["signal_type"]
    if invalidated:
        action = "INVALIDAR"
    elif any(word in signal_type for word in ["compra", "entrada"]):
        action = "OPERAR_COMPRA"
    elif "venda" in signal_type:
        action = "OPERAR_VENDA"
    else:
        action = "AGUARDAR"
    return {
        "action": action,
        "probability": quality["probability"],
        "quality": quality["quality"],
        "invalidated": invalidated,
        "auto_recalc": True,
        "dominant_structure": smc.get("structure", {}).get("trend", "neutral"),
    }


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
        analysis, confluence = build_multi_timeframe(symbol)
        consolidated = "NEUTRO"
        if confluence["strong_signal_allowed"] and confluence["dominant_direction"] == "BULLISH":
            consolidated = "COMPRA_FORTE_CONFIRMADA"
        elif confluence["strong_signal_allowed"] and confluence["dominant_direction"] == "BEARISH":
            consolidated = "VENDA_FORTE_CONFIRMADA"

        return jsonify({
            "success": True,
            "symbol": normalize_symbol(symbol),
            "analysis": analysis,
            "confluence": confluence,
            "consolidated_signal": consolidated,
            "alignment": f"{confluence['confirmed_timeframes']}/{len(HEATMAP_TIMEFRAMES)}",
        })
    except Exception as error:
        return jsonify({"success": False, "error": str(error)}), 400


@app.route("/api/live/status/<symbol>/<timeframe>")
@login_required
def api_live_status(symbol, timeframe):
    symbol = normalize_symbol(symbol)
    timeframe = normalize_timeframe(timeframe)
    cache_key = f"live:{symbol}:{timeframe}"
    cached = live_status_cache.get(cache_key)
    if cached is not None:
        return jsonify(sanitize_json(cached))
    try:
        df = load_market_data(symbol, timeframe, 260)
        ticker = get_ticker(symbol)
        status = build_live_status(df, symbol, timeframe, ticker)
        mtf_confluence = {}
        try:
            _, mtf_confluence = build_multi_timeframe(symbol)
        except Exception:
            mtf_confluence = {"strong_signal_allowed": True}
        status.update({
            "market": market.last_meta(symbol).get("market"),
            "market_label": market.last_meta(symbol).get("market_label"),
            "source": market.last_meta(symbol).get("source"),
            "market_data_status": market.last_meta(symbol).get("market_status"),
            "market_message": market.last_meta(symbol).get("message"),
            "streaming": market.last_meta(symbol).get("streaming", False),
        })
        status["signal_event"] = live_signal_manager.update_from_live_status(status, market.last_meta(symbol), mtf_confluence)
        live_status_cache.set(cache_key, status)
        return jsonify(sanitize_json(status))
    except Exception as error:
        fallback = {
            "success": False,
            "error": str(error),
            "symbol": symbol,
            "timeframe": timeframe,
            "state": "ANALYZING",
            "status": "ANALISANDO",
            "message": "IA recalculando. Grafico permanece ativo.",
            "messages": ["Analisando estrutura do mercado...", "Se a IA falhar, mantenha o grafico como referencia visual."],
            "confluence_score": 0,
            "confidence": 0,
            "probable_direction": "NEUTRAL",
            "trend_strength": 0,
            "volume_strength": 0,
            "risk_reward": 0,
            "entry_aggressive": None,
            "entry_conservative": None,
            "stop_loss": None,
            "take_profit": None,
            "reason": "Leitura live indisponivel no momento.",
            "invalidations": ["IA live indisponivel temporariamente."],
            "alerts": [],
            "disclaimer": "Analise educativa. Nao e recomendacao financeira. Toda operacao envolve risco.",
        }
        return jsonify(sanitize_json(fallback)), 200


@app.route("/api/live/signals")
@login_required
def api_live_signals():
    symbol = normalize_symbol(request.args.get("symbol", DEFAULT_SYMBOL))
    timeframe = normalize_timeframe(request.args.get("timeframe", "15m"))
    cache_key = f"live-signals:{symbol}:{timeframe}"
    cached = live_status_cache.get(cache_key)
    if cached is not None:
        return jsonify(sanitize_json(cached))
    try:
        df = load_market_data(symbol, timeframe, 260)
        ticker = get_ticker(symbol)
        status = build_live_status(df, symbol, timeframe, ticker)
        mtf_confluence = {}
        try:
            _, mtf_confluence = build_multi_timeframe(symbol)
        except Exception:
            mtf_confluence = {"strong_signal_allowed": True}
        status.update({
            "market": market.last_meta(symbol).get("market"),
            "market_label": market.last_meta(symbol).get("market_label"),
            "source": market.last_meta(symbol).get("source"),
            "market_data_status": market.last_meta(symbol).get("market_status"),
            "streaming": market.last_meta(symbol).get("streaming", False),
        })
        current_signal = live_signal_manager.update_from_live_status(status, market.last_meta(symbol), mtf_confluence)
        response = {
            "success": True,
            "signal": current_signal,
            "active": live_signal_manager.list_active(),
            "stats": live_signal_manager.stats(),
            "disclaimer": "Analise educativa. Nao constitui recomendacao financeira. Toda operacao envolve risco.",
        }
        live_status_cache.set(cache_key, response)
        return jsonify(sanitize_json(response))
    except Exception as error:
        return jsonify({"success": False, "error": str(error), "active": live_signal_manager.list_active(), "stats": live_signal_manager.stats()}), 200


@app.route("/api/live/signals/active")
@login_required
def api_live_signals_active():
    return jsonify(sanitize_json({
        "success": True,
        "active": live_signal_manager.list_active(),
        "stats": live_signal_manager.stats(),
        "disclaimer": "Analise educativa. Nao constitui recomendacao financeira. Toda operacao envolve risco.",
    }))


@app.route("/api/live/signals/history")
@login_required
def api_live_signals_history():
    limit = int(request.args.get("limit", 100))
    return jsonify(sanitize_json({
        "success": True,
        "history": live_signal_manager.list_history(limit),
        "stats": live_signal_manager.stats(),
    }))


@app.route("/api/replay/start", methods=["POST"])
@login_required
def api_replay_start():
    payload = request.get_json(silent=True) or {}
    symbol = normalize_symbol(payload.get("symbol", DEFAULT_SYMBOL))
    timeframe = normalize_timeframe(payload.get("timeframe", "15m"))
    speed = int(payload.get("speed", 1))
    try:
        df = load_market_data(symbol, timeframe, 1000)
        meta = market.last_meta(symbol)
        session = replay_engine.create(
            candles=df,
            symbol=symbol,
            market=meta.get("market"),
            timeframe=timeframe,
            start_date=payload.get("start_date"),
            end_date=payload.get("end_date"),
            speed=speed,
        )
        return jsonify(sanitize_json(session.start()))
    except Exception as error:
        return jsonify({"success": False, "error": str(error), "message": "Nao foi possivel iniciar o replay para o periodo escolhido."}), 200


@app.route("/api/replay/step", methods=["POST"])
@login_required
def api_replay_step():
    payload = request.get_json(silent=True) or {}
    try:
        session = replay_engine.get(payload.get("session_id"))
        return jsonify(sanitize_json(session.step(int(payload.get("direction", 1)))))
    except Exception as error:
        return jsonify({"success": False, "error": str(error)}), 200


@app.route("/api/replay/pause", methods=["POST"])
@login_required
def api_replay_pause():
    payload = request.get_json(silent=True) or {}
    try:
        session = replay_engine.get(payload.get("session_id"))
        return jsonify(sanitize_json(session.pause()))
    except Exception as error:
        return jsonify({"success": False, "error": str(error)}), 200


@app.route("/api/replay/reset", methods=["POST"])
@login_required
def api_replay_reset():
    payload = request.get_json(silent=True) or {}
    try:
        session = replay_engine.get(payload.get("session_id"))
        return jsonify(sanitize_json(session.reset()))
    except Exception as error:
        return jsonify({"success": False, "error": str(error)}), 200


@app.route("/api/replay/status")
@login_required
def api_replay_status():
    try:
        session = replay_engine.get(request.args.get("session_id"))
        return jsonify(sanitize_json(session.status()))
    except Exception as error:
        return jsonify({"success": False, "error": str(error)}), 200


@app.route("/api/replay/results")
@login_required
def api_replay_results():
    try:
        session = replay_engine.get(request.args.get("session_id"))
        return jsonify(sanitize_json(session.results()))
    except Exception as error:
        return jsonify({"success": False, "error": str(error)}), 200


@app.route("/api/operacional/analysis/<symbol>/<timeframe>")
@login_required
def api_operacional_analysis(symbol, timeframe):
    symbol = normalize_symbol(symbol)
    timeframe = normalize_timeframe(timeframe)
    try:
        limit = int(request.args.get("limit", 240))
        df = load_market_data(symbol, timeframe, limit)
        meta = market.last_meta(symbol)
        payload = build_operacional_reading(df, symbol, timeframe)
        payload.update({
            "source": meta.get("source"),
            "market": meta.get("market"),
            "market_label": meta.get("market_label"),
            "market_status": meta.get("market_status"),
            "market_message": meta.get("message"),
        })
        return jsonify(sanitize_json(payload))
    except Exception as error:
        return jsonify(sanitize_json({
            "success": False,
            "module": "operacional_leitura_grafica",
            "symbol": symbol,
            "timeframe": timeframe,
            "error": str(error),
            "narrative": ["Nao foi possivel gerar leitura operacional para este ativo agora."],
        })), 200


@app.route("/api/operacional/context/<symbol>/<timeframe>")
@login_required
def api_operacional_context(symbol, timeframe):
    symbol = normalize_symbol(symbol)
    timeframe = normalize_timeframe(timeframe)
    try:
        limit = int(request.args.get("limit", 240))
        df = load_market_data(symbol, timeframe, limit)
        return jsonify(sanitize_json(build_operacional_context(df, symbol, timeframe)))
    except Exception as error:
        return jsonify({"success": False, "symbol": symbol, "timeframe": timeframe, "error": str(error)}), 200


@app.route("/api/operacional/candle-flow/<symbol>/<timeframe>")
@login_required
def api_operacional_candle_flow(symbol, timeframe):
    symbol = normalize_symbol(symbol)
    timeframe = normalize_timeframe(timeframe)
    try:
        limit = int(request.args.get("limit", 120))
        df = load_market_data(symbol, timeframe, limit)
        return jsonify(sanitize_json(build_candle_flow(df, symbol, timeframe)))
    except Exception as error:
        return jsonify({"success": False, "symbol": symbol, "timeframe": timeframe, "error": str(error), "candle_flow": []}), 200


@app.route("/api/heatmap")
def get_heatmap():
    try:
        assets = market.get_assets()
        selected = request.args.get("symbols")
        if selected:
            requested = [normalize_symbol(item) for item in selected.split(",") if item.strip()]
            assets = [asset for asset in assets if asset["symbol"] in requested] or assets

        assets = assets[:8]
        heatmap = create_mtf_heatmap(assets, HEATMAP_TIMEFRAMES)
        return jsonify({"success": True, "source": "router", "heatmap": heatmap})
    except Exception as error:
        return jsonify({"success": False, "error": str(error)}), 400


def create_mtf_heatmap(assets, timeframes):
    colors = {
        "BULLISH": "#10b981",
        "BEARISH": "#ef4444",
        "NEUTRAL": "#64748b",
    }
    heatmap = {}
    for asset in assets:
        symbol = asset["symbol"] if isinstance(asset, dict) else asset
        analysis, confluence = build_multi_timeframe(symbol, timeframes)
        heatmap[symbol] = {}
        for timeframe, item in analysis.items():
            direction = item["direction"]
            heatmap[symbol][timeframe] = {
                "trend": item["trend"],
                "signal": item["signal"],
                "technical_signal": item["technical_signal"],
                "direction": direction,
                "strength": item["strength"],
                "confidence": item["confidence"],
                "color": colors.get(direction, "#64748b"),
            }
        heatmap[symbol]["confluence"] = confluence
    return heatmap


@app.route("/api/backtest/<symbol>")
def run_backtest(symbol):
    try:
        timeframe = request.args.get("timeframe", "1h")
        df = load_market_data(symbol, timeframe, 1000)
        market_meta = market.last_meta(symbol)
        result = ProfessionalBacktest(df, initial_capital=10000, risk_per_trade=0.01).run()
        result["pattern_learning"] = PatternLearner().summarize(result)
        return jsonify({"success": True, "source": market_meta.get("source"), "market": market_meta.get("market"), "symbol": normalize_symbol(symbol), "backtest": result})
    except Exception as error:
        return jsonify({"success": False, "error": str(error)}), 400


@app.route("/api/smc/<symbol>/<timeframe>")
def get_smc(symbol, timeframe):
    try:
        df = load_market_data(symbol, timeframe, 600)
        return jsonify({"success": True, "symbol": normalize_symbol(symbol), "timeframe": timeframe, "smc": analyze_smart_money(df)})
    except Exception as error:
        return jsonify({"success": False, "error": str(error)}), 400


@app.route("/api/watchlist", methods=["GET", "POST", "DELETE"])
@login_required
def api_watchlist():
    user_id = current_user_id()
    if request.method == "GET":
        symbols = get_watchlist(user_id)
        if not symbols:
            symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
        return jsonify({"success": True, "watchlist": symbols})
    payload = request.get_json(silent=True) or {}
    symbol = normalize_symbol(payload.get("symbol", ""))
    if request.method == "POST":
        return jsonify({"success": True, "watchlist": add_watchlist(user_id, symbol)})
    return jsonify({"success": True, "watchlist": remove_watchlist(user_id, symbol)})


@app.route("/api/alerts", methods=["GET", "POST"])
@login_required
def api_alerts():
    user_id = current_user_id()
    if request.method == "GET":
        return jsonify({"success": True, "alerts": list_alerts(user_id)})
    payload = request.get_json(silent=True) or {}
    alert_id = create_alert(
        user_id,
        normalize_symbol(payload.get("symbol", "BTCUSDT")),
        payload.get("condition_type", "price_above"),
        float(payload.get("target", 0)),
    )
    return jsonify({"success": True, "alert_id": alert_id, "alerts": list_alerts(user_id)})


@app.route("/api/telegram/settings", methods=["POST"])
@login_required
def api_telegram_settings():
    payload = request.get_json(silent=True) or {}
    save_setting(current_user_id(), "telegram", {
        "bot_token": payload.get("bot_token", ""),
        "chat_id": payload.get("chat_id", ""),
    })
    return jsonify({"success": True})


@app.route("/api/telegram/test", methods=["POST"])
@login_required
def api_telegram_test():
    settings = get_setting(current_user_id(), "telegram", {})
    if not settings.get("bot_token") or not settings.get("chat_id"):
        return jsonify({"success": False, "error": "telegram_not_configured"}), 400
    message = (request.get_json(silent=True) or {}).get("message", "FinanceAI institucional online.")
    result = send_telegram(settings["bot_token"], settings["chat_id"], message)
    return jsonify(result)


def send_telegram(bot_token, chat_id, message):
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": message},
            timeout=10,
        )
        return {"success": response.ok, "status_code": response.status_code, "response": response.json()}
    except Exception as error:
        return {"success": False, "error": str(error)}


@app.route("/api/assets")
def get_assets():
    market_key = request.args.get("market")
    return jsonify({
        "success": True,
        "source": "router",
        "markets": market.get_markets(),
        "assets": market.get_assets(market_key),
    })


@app.route("/api/timeframes")
def get_timeframes():
    return jsonify({"success": True, "timeframes": SUPPORTED_TIMEFRAMES})


if __name__ == "__main__":
    app.run(debug=True)
