"""Auxiliary OKX 5m risk watch. Never generates trade signals or orders.

Optional 5m collection failures must not block the original 2h/1h/15m
scanner or its publication.
"""
from __future__ import annotations

import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from collector import candles

OUT = Path("output")


def watch(symbol, five, fifteen, hourly):
    if min(len(five), len(fifteen), len(hourly)) < 30:
        return None
    last = five[-1]
    close = float(last[4])
    baseline = statistics.median(float(c[6]) for c in five[-21:-1])
    relvol = round(float(last[6]) / baseline, 3) if baseline > 0 else 0
    change = round(100 * (close / float(last[1]) - 1), 3)
    support = min(float(c[3]) for c in fifteen[-13:-1])
    resistance = max(float(c[2]) for c in fifteen[-13:-1])
    support_tests = sum(float(c[3]) <= support * 1.004 for c in fifteen[-13:-1])
    resistance_tests = sum(float(c[2]) >= resistance * 0.996 for c in fifteen[-13:-1])
    weak = (float(fifteen[-1][4]) < statistics.mean(float(c[4]) for c in fifteen[-20:])
            or float(hourly[-1][4]) < statistics.mean(float(c[4]) for c in hourly[-20:]))
    strong = (float(fifteen[-1][4]) > statistics.mean(float(c[4]) for c in fifteen[-20:])
              or float(hourly[-1][4]) > statistics.mean(float(c[4]) for c in hourly[-20:]))
    alerts = []

    if close < support * 0.9995 and relvol >= 1.5:
        alerts.append(("downside_risk", "high", support,
                       "5m fechado abaixo do suporte recente com volume elevado"))
    elif (close <= support * 1.004 and support_tests >= 2 and weak
          and (relvol >= 1.2 or change < -0.25)):
        alerts.append(("downside_risk", "watch", support,
                       "suporte testado repetidamente com enfraquecimento"))
    elif change <= -0.7 and relvol >= 2.0 and weak:
        alerts.append(("downside_risk", "high", support,
                       "queda acelerada de 5m com volume acima do normal"))

    if close > resistance * 1.0005 and relvol >= 1.5:
        alerts.append(("upside_risk", "high", resistance,
                       "5m fechado acima da resistencia recente com volume elevado"))
    elif (close >= resistance * 0.996 and resistance_tests >= 2 and strong
          and (relvol >= 1.2 or change > 0.25)):
        alerts.append(("upside_risk", "watch", resistance,
                       "resistencia testada repetidamente com pressao compradora"))
    elif change >= 0.7 and relvol >= 2.0 and strong:
        alerts.append(("upside_risk", "high", resistance,
                       "alta acelerada de 5m com volume acima do normal"))

    if not alerts:
        return None
    direction, severity, level, reason = sorted(
        alerts, key=lambda x: x[1] == "high", reverse=True
    )[0]
    return {
        "symbol": symbol, "risk_direction": direction, "severity": severity,
        "reason": reason, "reference_level": round(level, 10),
        "last_closed_5m_open_ms": int(last[0]), "last_closed_5m_close": close,
        "change_last_5m_pct": change, "relative_quote_volume_5m": relvol,
        "support_tests_15m": support_tests,
        "resistance_tests_15m": resistance_tests,
        "trade_signal": False, "suggest_manual_reanalysis": severity == "high",
    }


def main():
    data = json.loads((OUT / "market_data.json").read_text(encoding="utf-8"))
    result = {
        "schema_version": 1, "status": "OK",
        "source_collected_utc": data["collected_utc"],
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "timeframe": "5m_closed_only", "trade_signals_allowed": False,
        "method": "exploratory_support_resistance_volume_watch_not_prediction",
        "scanned": 0, "warnings": [], "rejected": {},
    }
    now_ms = int(time.time() * 1000)
    deadline = time.monotonic() + 120
    for item in data["universe"]:
        if time.monotonic() >= deadline:
            break
        symbol = item["symbol"]
        try:
            bars = candles(symbol, "5m", now_ms)
            if now_ms - (int(bars[-1][0]) + 300_000) > 600_000:
                raise ValueError("5m data too old")
            path = OUT / "coins" / (symbol + ".json")
            detail = json.loads(path.read_text(encoding="utf-8"))
            detail["candles"]["5m"] = bars
            detail["auxiliary_5m_closed_only"] = True
            path.write_text(json.dumps(detail, ensure_ascii=False, separators=(",", ":")),
                            encoding="utf-8")
            result["scanned"] += 1
            alert = watch(symbol, bars, data["candles"][symbol]["15m"],
                          data["candles"][symbol]["1h"])
            if alert:
                result["warnings"].append(alert)
        except (ValueError, RuntimeError, KeyError, TypeError, OSError) as exc:
            result["rejected"][symbol] = str(exc)
        time.sleep(0.08)

    result["warnings"].sort(
        key=lambda a: (a["severity"] == "high", a["relative_quote_volume_5m"]),
        reverse=True
    )
    result["warnings"] = result["warnings"][:12]
    if not result["scanned"]:
        result["status"] = "UNAVAILABLE"
    (OUT / "early_warning.json").write_text(
        json.dumps(result, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8"
    )
    path = OUT / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["early_warning_file"] = "early_warning.json"
    manifest["auxiliary_5m_coverage"] = result["scanned"]
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    print(f"EARLY WATCH: {result['scanned']} assets with 5m; "
          f"{len(result['warnings'])} warnings (not trade signals).")


if __name__ == "__main__":
    main()
