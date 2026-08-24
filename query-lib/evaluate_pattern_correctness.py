"""Compare OC-M3 EKG occurrence exports with synthetic ground truth."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Callable, Dict, Iterable, Optional, Set, Tuple

import pandas as pd


OccurrenceKey = Tuple[str, ...]


def _text(value: object) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip()


def _perspective(value: object) -> str:
    return _text(value).lower()


def _ordered_pair(left: object, right: object) -> Tuple[str, str]:
    return tuple(sorted((_text(left), _text(right))))


def _detected_handover(row: pd.Series) -> OccurrenceKey:
    return (
        _perspective(row["perspective"]),
        _text(row["objective_id"]),
        _text(row["event_i"]),
        _text(row["event_j"]),
        _text(row["from_robot_id"]),
        _text(row["to_robot_id"]),
    )


def _expected_handover(row: pd.Series) -> OccurrenceKey:
    return (
        _perspective(row["objective_type"]),
        _text(row["objective_id"]),
        _text(row["prev_event_id"]),
        _text(row["next_event_id"]),
        _text(row["from_robot_id"]),
        _text(row["to_robot_id"]),
    )


def _detected_switch(row: pd.Series) -> OccurrenceKey:
    return (
        _perspective(row["perspective"]),
        _text(row["robot_id"]),
        _text(row["event_i"]),
        _text(row["event_j"]),
        _text(row["from_objective_id"]),
        _text(row["to_objective_id"]),
    )


def _expected_switch(row: pd.Series) -> OccurrenceKey:
    return (
        _perspective(row["objective_type"]),
        _text(row["robot_id"]),
        _text(row["prev_event_id"]),
        _text(row["next_event_id"]),
        _text(row["from_objective_id"]),
        _text(row["to_objective_id"]),
    )


def _detected_return(row: pd.Series) -> OccurrenceKey:
    return (
        _perspective(row["perspective"]),
        _text(row["objective_id"]),
        _text(row["event_i"]),
        _text(row["event_j"]),
        _text(row["event_k"]),
        _text(row["returning_robot_id"]),
        _text(row["intermediate_robot_id"]),
    )


def _expected_return(row: pd.Series) -> OccurrenceKey:
    return (
        _perspective(row["objective_type"]),
        _text(row["objective_id"]),
        _text(row["event_i"]),
        _text(row["event_j"]),
        _text(row["event_k"]),
        _text(row["returning_robot_id"]),
        _text(row["intermediate_robot_id"]),
    )


def _detected_parallel(row: pd.Series) -> OccurrenceKey:
    perspective = _perspective(row["perspective"])
    if perspective == "mission":
        left, right = _ordered_pair(row["mission_id"], row["second_mission_id"])
        mission = ""
    else:
        left, right = _ordered_pair(row["segment_1_id"], row["segment_2_id"])
        mission = _text(row["mission_id"])
    return perspective, mission, left, right


def _expected_parallel(row: pd.Series) -> OccurrenceKey:
    perspective = _perspective(row["objective_type"])
    left, right = _ordered_pair(row["objective_1"], row["objective_2"])
    mission = _text(row["mission_id"]) if perspective == "segment" else ""
    return perspective, mission, left, right


PATTERN_SPECS: Dict[str, Tuple[str, str, Callable[[pd.Series], OccurrenceKey], Callable[[pd.Series], OccurrenceKey]]] = {
    "Robot handover": (
        "all_handover_occurrences.csv",
        "handover_occurrences.csv",
        _detected_handover,
        _expected_handover,
    ),
    "Objective switch": (
        "all_switch_occurrences.csv",
        "switch_occurrences.csv",
        _detected_switch,
        _expected_switch,
    ),
    "Capability-driven return": (
        "all_capability_return_occurrences.csv",
        "capability_return_occurrences.csv",
        _detected_return,
        _expected_return,
    ),
    "Parallel collaboration": (
        "all_parallel_occurrences.csv",
        "parallel_occurrences.csv",
        _detected_parallel,
        _expected_parallel,
    ),
}


def _ground_truth_file(directory: Path, combined_name: str, local_name: str) -> Path:
    combined = directory / combined_name
    if combined.exists():
        return combined
    local = directory / local_name
    if local.exists():
        return local
    raise FileNotFoundError(f"Missing {combined_name} or {local_name} in {directory}")


def _keys(rows: Iterable[Tuple[int, pd.Series]], key_fn: Callable[[pd.Series], OccurrenceKey]) -> Set[OccurrenceKey]:
    return {key_fn(row) for _, row in rows}


def evaluate(
    detected_path: Path,
    ground_truth_dir: Path,
    strategy: str,
    case_study: str,
    algorithm: str,
) -> pd.DataFrame:
    detected = pd.read_csv(detected_path, dtype=str).fillna("")
    detected = detected[detected["strategy"] == strategy]
    results = []

    for pattern, (combined_name, local_name, detected_key, expected_key) in PATTERN_SPECS.items():
        detected_pattern = detected[detected["structure"] == pattern]
        expected = pd.read_csv(
            _ground_truth_file(ground_truth_dir, combined_name, local_name),
            dtype=str,
        ).fillna("")
        if "case_study" in expected.columns:
            expected = expected[expected["case_study"] == case_study]
        if "algorithm" in expected.columns:
            expected = expected[expected["algorithm"] == algorithm]

        detected_keys = _keys(detected_pattern.iterrows(), detected_key)
        expected_keys = _keys(expected.iterrows(), expected_key)
        true_positive = len(detected_keys & expected_keys)
        false_positive = len(detected_keys - expected_keys)
        false_negative = len(expected_keys - detected_keys)
        precision: Optional[float] = (
            true_positive / len(detected_keys) if detected_keys else None
        )
        recall: Optional[float] = true_positive / len(expected_keys) if expected_keys else None
        f1 = (
            2 * precision * recall / (precision + recall)
            if precision is not None and recall is not None and precision + recall > 0
            else None
        )
        results.append(
            {
                "pattern": pattern,
                "expected": len(expected_keys),
                "detected": len(detected_keys),
                "true_positive": true_positive,
                "false_positive": false_positive,
                "false_negative": false_negative,
                "precision": precision,
                "recall": recall,
                "f1": f1,
            }
        )
    return pd.DataFrame(results)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare OC-M3 occurrences.csv with synthetic ground-truth occurrence tables."
    )
    parser.add_argument("--detected", required=True, type=Path, help="Exported occurrences.csv")
    parser.add_argument("--ground-truth-dir", required=True, type=Path)
    parser.add_argument("--strategy", required=True, help="Log/strategy value in occurrences.csv")
    parser.add_argument("--case-study", required=True)
    parser.add_argument("--algorithm", required=True)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    result = evaluate(
        args.detected,
        args.ground_truth_dir,
        args.strategy,
        args.case_study,
        args.algorithm,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(args.out, index=False)
    print(result.to_string(index=False))
    print(f"Wrote correctness results to {args.out}")


if __name__ == "__main__":
    main()
