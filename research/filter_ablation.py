"""Audit why archived BREAKOUT/RETEST confirmations were blocked.

One-filter-at-a-time is a DESCRIPTIVE count of gates. It does not disable
any live restriction, execute counterfactual fills, or measure filter alpha.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path

from research.point_in_time_candidates import inspect_snapshots

# Map the exact reasons emitted by entry_trigger.analyze_method.
# Unknown reasons are *never* treated as safely removable.
FILTERS = {
    "spread_above_limit": ("spread acima do limite", "risk_guard"),
    "stop_above_3atr": ("stop distante > 3 ATR", "risk_guard"),
    "trigger_already_passed_candle": (
        "gatilho ja ultrapassado no candle: nao perseguir", "temporal_guard"),
    "trigger_far_above_0_9atr": ("gatilho distante > 0.9 ATR", "strategy"),
    "rr_below_1_6": ("R:R liquido inferior a 1.6 na entrada exata", "strategy"),
    "pivot_before_tp1": ("pivot estrutural entre entrada e TP1", "strategy"),
    "price_crossed_trigger": (
        "cotacao da coleta ja atravessou o preco de gatilho", "temporal_guard"),
    "price_crossed_stop": (
        "cotacao da coleta cruzou nivel de invalidacao/stop", "temporal_guard"),
    "opposed_1h": (
        "1h em tendencia contraria sem virada estrutural", "strategy"),
    "opposed_2h": (
        "2h contraria: setup exige analise adicional", "strategy"),
    "stale_or_expired": (
        "snapshot/candle expirado: recalcular com coleta atual", "temporal_guard"),
    "invalid_tick": ("tickSz oficial OKX ausente ou invalido", "data_guard"),
}
SPECIAL_PREFIXES = {
    "uncalculable_plan": "plano nao calculavel:",
    "unconfirmed": "candle de confirmacao ainda nao ocorreu",
    "no_prior_breakout": "rompimento anterior nao identificado",
}
INFO_PREFIX = "Aguardar preco cruzar gatilho e validar spread/volume novamente"
REASONS_TO_KEY = {v[0]: k for k,v in FILTERS.items()}
STRATEGY_FILTERS = tuple(k for k,v in FILTERS.items() if v[1] == "strategy")
SAFETY_FILTERS = tuple(k for k,v in FILTERS.items() if v[1] != "strategy")


def classify_reason(reason):
    if reason in REASONS_TO_KEY:
        return REASONS_TO_KEY[reason]
    if reason == INFO_PREFIX:
        return "informational"
    for key, prefix in SPECIAL_PREFIXES.items():
        if reason.startswith(prefix):
            return key
    return "unknown_reason"


def classify_candidate(candidate):
    reasons = candidate.get("reasons")
    if not isinstance(reasons, list):
        raise ValueError("candidate has no reason list")
    seen = sorted({classify_reason(reason) for reason in reasons})
    if candidate["decision_at_snapshot"] == "GATILHO_CONDICIONAL":
        if seen != ["informational"]:
            raise ValueError("unexpected allowed candidate reasons")
        return ()
    blockers = tuple(x for x in seen if x != "informational")
    if not blockers:
        raise ValueError("blocked candidate lacks reason")
    return blockers


def analyze_report(snapshot_report):
    candidates = snapshot_report["candidates"]
    confirmed = snapshot_report["confirmed_candidates"]
    if len(candidates) != confirmed:
        raise ValueError("source candidate count differs")
    counts = Counter()
    by_method = defaultdict(Counter)
    by_side = defaultdict(Counter)
    by_filter = defaultdict(Counter)
    overlaps = Counter()
    exclusive = Counter()
    exclusive_ids = defaultdict(list)
    multi = 0
    rows = []
    for candidate in candidates:
        method, side = candidate["method"], candidate["side"]
        if method not in ("BREAKOUT","RETEST") or side not in ("LONG","SHORT"):
            raise ValueError("unexpected method/side")
        blockers = classify_candidate(candidate)
        if candidate["decision_at_snapshot"] == "GATILHO_CONDICIONAL":
            counts["conditional_baseline"] += 1
            by_method[method]["conditional_baseline"] += 1
            by_side[side]["conditional_baseline"] += 1
        else:
            counts["blocked_baseline"] += 1
            by_method[method]["blocked_baseline"] += 1
            by_side[side]["blocked_baseline"] += 1
        by_method[method]["confirmed"] += 1
        by_side[side]["confirmed"] += 1
        counts["confirmed"] += 1
        for blocker in blockers:
            by_filter[blocker]["mentions"] += 1
            by_filter[blocker][f"method_{method}"] += 1
            by_filter[blocker][f"side_{side}"] += 1
        if len(blockers) > 1:
            multi += 1
        if blockers:
            overlaps["|".join(blockers)] += 1
        if len(blockers) == 1:
            only = blockers[0]
            exclusive[only] += 1
            by_filter[only]["sole_blocker"] += 1
            if only in STRATEGY_FILTERS:
                by_filter[only]["would_pass_other_recorded_gates"] += 1
                exclusive_ids[only].append({
                    "symbol": candidate["symbol"],
                    "side": side, "method": method,
                    "signal_close_ms": candidate["signal_close_ms"],
                    "first_observed_at_ms": candidate["first_observed_at_ms"],
                    "entry": candidate["entry"], "stop": candidate["stop"],
                    "tp1": candidate["tp1"],
                    "spread_pct_observed": candidate["spread_pct_observed"],
                    "counterfactual_only": True,
                    "exchange_fill_verified": False,
                })
        rows.append({
            "symbol":candidate["symbol"], "method":method, "side":side,
            "signal_close_ms":candidate["signal_close_ms"],
            "first_observed_at_ms":candidate["first_observed_at_ms"],
            "baseline_decision":candidate["decision_at_snapshot"],
            "blockers":list(blockers),
            "exclusively_blocked_by":blockers[0] if len(blockers)==1 else None,
            "outcome_studied":False, "counterfactual_fill_verified":False,
        })
    if counts["confirmed"] != counts["blocked_baseline"]+counts["conditional_baseline"]:
        raise AssertionError("invalid baseline decomposition")
    if sum(exclusive.values())+multi != counts["blocked_baseline"]:
        raise AssertionError("invalid blocker accounting")
    return {
        "status":"DESCRIPTIVE_FILTER_ABLATION_NOT_PERFORMANCE_TEST",
        "snapshot_count":snapshot_report["snapshots_analyzed"],
        "candidate_count":len(candidates),
        "baseline":dict(counts),
        "by_method":{k:dict(v) for k,v in sorted(by_method.items())},
        "by_side":{k:dict(v) for k,v in sorted(by_side.items())},
        "filters":{k:{"category": FILTERS[k][1] if k in FILTERS else
                             "unrecognized_or_invalid",
                       "mentions":v["mentions"],
                       "sole_blocker":v["sole_blocker"],
                       "would_pass_other_recorded_gates":v["would_pass_other_recorded_gates"],
                       "by_method":{"BREAKOUT":v["method_BREAKOUT"],
                                    "RETEST":v["method_RETEST"]},
                       "by_side":{"LONG":v["side_LONG"],
                                  "SHORT":v["side_SHORT"]}}
                   for k,v in sorted(by_filter.items())},
        "multi_blocker_candidates":multi,
        "blocker_combination_counts":dict(overlaps.most_common()),
        "hypothetical_single_strategy_filter_removal_candidates":{
            k: len(exclusive_ids[k]) for k in STRATEGY_FILTERS},
        "counterfactual_exclusive_candidates":dict(exclusive_ids),
        "per_candidate":rows,
        "production_gates_modified":False, "profitability_proven":False,
        "warnings":[
            "Counts are from confirmed candidates only; volume/body/directional-close confirmation gates were not ablated",
            "Mention counts overlap; they do not represent independent blocked opportunities",
            "Removing one strategy gate means the candidate passes only OTHER RECORDED gates, not an execution check",
            "Data, risk and temporal guards are non-removable by this analysis",
            "No causal effect or false-positive rate inferred from descriptive gate counts",
            "Counterfactual candidates would require post-snapshot 1m evaluation with explicit assumptions",
            "No exchange orders or verified fills",
        ],
    }


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("snapshots",nargs="+")
    p.add_argument("--report",type=Path,required=True)
    args=p.parse_args(argv)
    report=analyze_report(inspect_snapshots(args.snapshots))
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print("FILTER_ABLATION", json.dumps({
        "baseline":report["baseline"],
        "by_method":report["by_method"],
        "filters":report["filters"],
        "multi_blocker_candidates":report["multi_blocker_candidates"],
        "hypothetical_single_strategy_filter_removal_candidates":
            report["hypothetical_single_strategy_filter_removal_candidates"],
        "profitability_proven":False,
    },sort_keys=True))


if __name__=="__main__":
    main()
