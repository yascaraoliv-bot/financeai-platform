"""
Smart Money Concepts para o FinanceAI.

Detecta BOS, CHOCH, order blocks, zonas de liquidez, fair value gaps,
sweep de liquidez, falso rompimento e zonas institucionais.
"""

import numpy as np
import pandas as pd


def _time(index):
    return int(pd.Timestamp(index).timestamp())


class SmartMoneyAnalyzer:
    def __init__(self, candles, swing_window=3):
        self.df = candles.copy().dropna(subset=["open", "high", "low", "close", "volume"])
        self.swing_window = swing_window
        if len(self.df) < swing_window * 2 + 20:
            raise ValueError("smart_money_requires_more_candles")

    def analyze(self, signal_type="neutro"):
        swings = self._swings()
        structure = self._structure(swings)
        order_blocks = self._order_blocks()
        liquidity_zones = self._liquidity_zones(swings)
        fair_value_gaps = self._fair_value_gaps()
        liquidity_sweep = self._liquidity_sweep(liquidity_zones)
        false_breakout = self._false_breakout()
        institutional_zones = self._institutional_zones(order_blocks, fair_value_gaps, liquidity_zones)
        nearest_order_block = self._nearest_zone(order_blocks)
        relevant_fvg = self._nearest_zone(fair_value_gaps)
        nearest_liquidity_zone = self._nearest_zone(liquidity_zones)
        score_adjustment = self._score_adjustment(
            signal_type,
            structure,
            nearest_order_block,
            relevant_fvg,
            nearest_liquidity_zone,
            liquidity_sweep,
            false_breakout,
        )
        confirmed = score_adjustment["confirmed"]
        invalidated = score_adjustment["invalidated"]

        return {
            "has_bos": structure["bos"] != "none",
            "bos": structure["bos"],
            "has_choch": structure["choch"] != "none",
            "choch": structure["choch"],
            "liquidity_zone": nearest_liquidity_zone,
            "nearest_order_block": nearest_order_block,
            "relevant_fvg": relevant_fvg,
            "liquidity_sweep": liquidity_sweep,
            "false_breakout": false_breakout,
            "institutional_zones": institutional_zones,
            "confirmed": confirmed,
            "invalidated": invalidated,
            "score_adjustment": score_adjustment["value"],
            "reasons": score_adjustment["reasons"],
            "structure": structure,
            "order_blocks": order_blocks,
            "liquidity": liquidity_zones,
            "fair_value_gaps": fair_value_gaps,
        }

    def _swings(self):
        highs = []
        lows = []
        w = self.swing_window
        for i in range(w, len(self.df) - w):
            window = self.df.iloc[i - w:i + w + 1]
            candle = self.df.iloc[i]
            if candle["high"] == window["high"].max():
                highs.append({"index": i, "time": _time(self.df.index[i]), "price": float(candle["high"])})
            if candle["low"] == window["low"].min():
                lows.append({"index": i, "time": _time(self.df.index[i]), "price": float(candle["low"])})
        return {"highs": highs, "lows": lows}

    def _structure(self, swings):
        highs = swings["highs"]
        lows = swings["lows"]
        close = float(self.df["close"].iloc[-1])
        previous_close = float(self.df["close"].iloc[-2])
        last_high = highs[-1] if highs else None
        last_low = lows[-1] if lows else None

        trend = "neutral"
        if len(highs) >= 2 and len(lows) >= 2:
            if highs[-1]["price"] > highs[-2]["price"] and lows[-1]["price"] > lows[-2]["price"]:
                trend = "bullish"
            elif highs[-1]["price"] < highs[-2]["price"] and lows[-1]["price"] < lows[-2]["price"]:
                trend = "bearish"

        bos = "none"
        choch = "none"
        break_level = None
        if last_high and previous_close <= last_high["price"] < close:
            bos = "bullish"
            break_level = last_high["price"]
            if trend == "bearish":
                choch = "bullish"
        elif last_low and previous_close >= last_low["price"] > close:
            bos = "bearish"
            break_level = last_low["price"]
            if trend == "bullish":
                choch = "bearish"

        return {
            "trend": trend,
            "bos": bos,
            "choch": choch,
            "break_level": float(break_level) if break_level else None,
            "last_high": last_high,
            "last_low": last_low,
        }

    def _atr(self, period=14):
        high_low = self.df["high"] - self.df["low"]
        high_close = (self.df["high"] - self.df["close"].shift()).abs()
        low_close = (self.df["low"] - self.df["close"].shift()).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        return tr.rolling(period).mean().bfill()

    def _order_blocks(self, lookback=120):
        blocks = []
        frame = self.df.tail(lookback)
        atr = float(self._atr().iloc[-1])
        atr = atr if atr > 0 else float(self.df["close"].iloc[-1]) * 0.01
        for i in range(2, len(frame) - 3):
            candle = frame.iloc[i]
            impulse = frame.iloc[i + 1:i + 4]
            move = float(impulse["close"].iloc[-1] - candle["close"])
            body = abs(float(candle["close"] - candle["open"]))
            is_bullish_ob = candle["close"] < candle["open"] and move > atr * 1.2
            is_bearish_ob = candle["close"] > candle["open"] and move < -atr * 1.2
            if body <= atr * 1.1 and (is_bullish_ob or is_bearish_ob):
                high = float(candle["high"])
                low = float(candle["low"])
                blocks.append({
                    "type": "bullish" if is_bullish_ob else "bearish",
                    "time": _time(frame.index[i]),
                    "high": high,
                    "low": low,
                    "mid": float((high + low) / 2),
                    "strength": round(min(abs(move) / atr, 3.0), 3),
                })
        return blocks[-10:]

    def _liquidity_zones(self, swings, tolerance=0.0018):
        zones = []

        def build(points, zone_type):
            used = set()
            for i, point in enumerate(points):
                if i in used:
                    continue
                cluster = [point]
                for j, other in enumerate(points[i + 1:], start=i + 1):
                    if abs(other["price"] - point["price"]) / point["price"] <= tolerance:
                        cluster.append(other)
                        used.add(j)
                if len(cluster) >= 2:
                    prices = [item["price"] for item in cluster]
                    zones.append({
                        "type": zone_type,
                        "price": float(np.mean(prices)),
                        "low": float(min(prices)),
                        "high": float(max(prices)),
                        "touches": len(cluster),
                        "time": cluster[-1]["time"],
                    })

        build(swings["highs"][-20:], "buy_side")
        build(swings["lows"][-20:], "sell_side")
        return sorted(zones[-12:], key=lambda item: item["touches"], reverse=True)

    def _fair_value_gaps(self, lookback=160):
        gaps = []
        frame = self.df.tail(lookback)
        for i in range(2, len(frame)):
            c1 = frame.iloc[i - 2]
            c3 = frame.iloc[i]
            if c1["high"] < c3["low"]:
                gaps.append({
                    "type": "bullish",
                    "time": _time(frame.index[i]),
                    "low": float(c1["high"]),
                    "high": float(c3["low"]),
                    "mid": float((c1["high"] + c3["low"]) / 2),
                })
            elif c1["low"] > c3["high"]:
                gaps.append({
                    "type": "bearish",
                    "time": _time(frame.index[i]),
                    "low": float(c3["high"]),
                    "high": float(c1["low"]),
                    "mid": float((c3["high"] + c1["low"]) / 2),
                })
        return gaps[-12:]

    def _liquidity_sweep(self, liquidity_zones):
        if not liquidity_zones:
            return {"detected": False, "side": "none", "zone": None}
        last = self.df.iloc[-1]
        for zone in liquidity_zones:
            if zone["type"] == "buy_side" and last["high"] > zone["high"] and last["close"] < zone["price"]:
                return {"detected": True, "side": "buy_side_sweep", "zone": zone}
            if zone["type"] == "sell_side" and last["low"] < zone["low"] and last["close"] > zone["price"]:
                return {"detected": True, "side": "sell_side_sweep", "zone": zone}
        return {"detected": False, "side": "none", "zone": None}

    def _false_breakout(self, lookback=24):
        frame = self.df.iloc[-lookback - 1:-1]
        last = self.df.iloc[-1]
        resistance = float(frame["high"].max())
        support = float(frame["low"].min())
        if last["high"] > resistance and last["close"] < resistance:
            return {"detected": True, "direction": "bullish_failed", "level": resistance}
        if last["low"] < support and last["close"] > support:
            return {"detected": True, "direction": "bearish_failed", "level": support}
        return {"detected": False, "direction": "none", "level": None}

    def _institutional_zones(self, order_blocks, fair_value_gaps, liquidity_zones):
        zones = []
        for block in order_blocks[-5:]:
            zones.append({**block, "kind": "order_block"})
        for gap in fair_value_gaps[-5:]:
            zones.append({**gap, "kind": "fair_value_gap"})
        for liquidity in liquidity_zones[:5]:
            zones.append({**liquidity, "kind": "liquidity"})
        return sorted(zones, key=lambda item: self._distance_to_price(item))[:10]

    def _nearest_zone(self, zones):
        if not zones:
            return None
        return sorted(zones, key=lambda item: self._distance_to_price(item))[0]

    def _distance_to_price(self, zone):
        price = float(self.df["close"].iloc[-1])
        reference = zone.get("mid", zone.get("price", (zone.get("high", price) + zone.get("low", price)) / 2))
        return abs(float(reference) - price) / price

    def _score_adjustment(self, signal_type, structure, order_block, fvg, liquidity_zone, sweep, false_breakout):
        signal = signal_type.lower()
        is_buy = any(word in signal for word in ["compra", "entrada", "buy"])
        is_sell = any(word in signal for word in ["venda", "sell"])
        value = 0
        reasons = []

        if structure["bos"] == "bullish":
            value += 8 if is_buy else -5 if is_sell else 2
            reasons.append("BOS comprador detectado.")
        elif structure["bos"] == "bearish":
            value -= 8 if is_sell else -5 if is_buy else 2
            reasons.append("BOS vendedor detectado.")

        if structure["choch"] == "bullish":
            value += 10 if is_buy else -8
            reasons.append("CHOCH comprador detectado.")
        elif structure["choch"] == "bearish":
            value -= 10 if is_sell else -8
            reasons.append("CHOCH vendedor detectado.")

        if order_block:
            if order_block["type"] == "bullish":
                value += 6 if is_buy else -3
                reasons.append("Order block comprador proximo.")
            elif order_block["type"] == "bearish":
                value -= 6 if is_sell else -3
                reasons.append("Order block vendedor proximo.")

        if fvg:
            if fvg["type"] == "bullish":
                value += 4 if is_buy else -2
                reasons.append("FVG comprador relevante.")
            elif fvg["type"] == "bearish":
                value -= 4 if is_sell else -2
                reasons.append("FVG vendedor relevante.")

        if liquidity_zone:
            reasons.append(f"Zona de liquidez {liquidity_zone['type']} proxima.")

        if sweep["detected"]:
            if sweep["side"] == "sell_side_sweep":
                value += 8 if is_buy else -5
                reasons.append("Sweep de liquidez vendedora favorece reversao compradora.")
            elif sweep["side"] == "buy_side_sweep":
                value -= 8 if is_sell else -5
                reasons.append("Sweep de liquidez compradora favorece reversao vendedora.")

        if false_breakout["detected"]:
            value -= 15
            reasons.append("Falso rompimento invalida a leitura direcional.")

        confirmed = value >= 8 and not false_breakout["detected"]
        invalidated = value <= -8 or false_breakout["detected"]
        return {"value": int(max(-25, min(25, value))), "confirmed": bool(confirmed), "invalidated": bool(invalidated), "reasons": reasons}


def analyze_smart_money(candles, signal_type="neutro"):
    return SmartMoneyAnalyzer(candles).analyze(signal_type)
