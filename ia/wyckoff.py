"""
Leitura Wyckoff simplificada para contexto operacional.
"""

import pandas as pd


class WyckoffReader:
    def __init__(self, candles):
        self.df = candles.copy().dropna(subset=["open", "high", "low", "close", "volume"])
        if len(self.df) < 60:
            raise ValueError("wyckoff_requires_at_least_60_candles")

    def read(self):
        recent = self.df.tail(80)
        last = self.df.iloc[-1]
        volume = self.df["volume"]
        avg_volume = volume.rolling(20).mean().bfill().iloc[-1]
        range_high = float(recent["high"].max())
        range_low = float(recent["low"].min())
        range_mid = (range_high + range_low) / 2
        close = float(last["close"])
        range_pct = (range_high - range_low) / max(close, 0.00000001) * 100
        volume_ratio = float(last["volume"] / max(avg_volume, 0.00000001))

        spring = last["low"] < range_low * 1.002 and close > range_low and volume_ratio >= 1.2
        upthrust = last["high"] > range_high * 0.998 and close < range_high and volume_ratio >= 1.2
        climax = volume_ratio >= 2.2 and abs(last["close"] - last["open"]) / max(last["high"] - last["low"], 0.00000001) >= 0.55
        test = volume_ratio < 0.85 and range_low < close < range_high

        if spring:
            phase = "spring"
            bias = "bullish"
        elif upthrust:
            phase = "upthrust"
            bias = "bearish"
        elif close < range_mid and range_pct < 9:
            phase = "acumulacao"
            bias = "bullish_neutral"
        elif close > range_mid and range_pct < 9:
            phase = "distribuicao"
            bias = "bearish_neutral"
        elif climax and close > last["open"]:
            phase = "buying_climax"
            bias = "caution_bullish"
        elif climax and close < last["open"]:
            phase = "selling_climax"
            bias = "caution_bearish"
        elif test:
            phase = "teste"
            bias = "neutral"
        else:
            phase = "markup" if close > range_mid else "markdown"
            bias = "bullish" if close > range_mid else "bearish"

        return {
            "phase": phase,
            "bias": bias,
            "accumulation": phase == "acumulacao",
            "distribution": phase == "distribuicao",
            "spring": bool(spring),
            "upthrust": bool(upthrust),
            "climax": bool(climax),
            "test": bool(test),
            "range": {
                "high": range_high,
                "low": range_low,
                "mid": range_mid,
                "range_pct": round(float(range_pct), 3),
            },
            "volume_ratio": round(volume_ratio, 3),
        }


def read_wyckoff(candles):
    return WyckoffReader(candles).read()
