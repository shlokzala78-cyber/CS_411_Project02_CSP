"""UIC CS 411 Project 2: University Class Scheduling (CSP) - Main Entry Point"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import platform
import re
import tempfile
from dataclasses import asdict
from pathlib import Path
from time import perf_counter

from backtracking_solver import BacktrackingSolver, NoSolutionError, SearchLimitError
from constraints_check import CSV_FIELDS, ConstraintEngine, validate_timetable
from data_loader import DataError, load_data
from local_search import MinConflictsSolver


def resolve_student_id(student_id=None):
    """Use an explicit ID, STUDENT_ID environment variable, or a safe demo label."""
    value = str(student_id if student_id is not None else os.environ.get("STUDENT_ID", "student")).strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", value):
        raise ValueError("student_id must be 1-80 letters, digits, hyphens, or underscores, starting with a letter/digit")
    return value


def _write_json(path, payload):
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=2, allow_nan=False)
        handle.write("\n")


def run_scheduler(data_dir="data", output_dir="data", student_id=None, *,
                  algorithm="hybrid", seed=None, local_steps=None):
    if algorithm not in {"backtracking", "hybrid", "min-conflicts"}:
        raise ValueError(f"Unknown solver {algorithm!r}")
    student_id = resolve_student_id(student_id)
    started = perf_counter()
    problem = load_data(data_dir)
    if seed is not None:
        if type(seed) is not int or seed < 0:
            raise ValueError("seed must be a nonnegative integer")
        problem.settings["seed"] = seed
    if local_steps is not None:
        if type(local_steps) is not int or local_steps < 0:
            raise ValueError("local_steps must be a nonnegative integer")
        problem.settings["local_steps"] = local_steps
    engine = ConstraintEngine(problem)
    settings = problem.settings
    csp = BacktrackingSolver(engine, max_nodes=settings["max_search_nodes"], max_seconds=settings["max_search_seconds"])
    initial = None if algorithm == "min-conflicts" else csp.solve()
    initial_penalties = engine.soft_penalties(initial) if initial is not None else None
    local_stats = None
    if algorithm in {"hybrid", "min-conflicts"}:
        local = MinConflictsSolver(csp, seed=settings["seed"], max_steps=settings["local_steps"],
                                  patience=settings["local_patience"], candidate_limit=settings["local_candidate_limit"])
        assignment = local.optimize(initial)
        local_stats = local.stats
    else:
        assignment = initial
    violations = engine.hard_violations(assignment)
    if violations:
        raise RuntimeError("Refusing to export invalid timetable: " + "; ".join(violations))
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    timetable = output / f"{student_id}_timetable.csv"
    diagnostics = output / f"{student_id}_diagnostics.json"
    weekdays = {day: n for n, day in enumerate(("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"))}
    ordered = sorted(assignment, key=lambda c: (weekdays[problem.slots[assignment[c].slot_id].day],
                                                problem.slots[assignment[c].slot_id].start, c))
    descriptor, temporary = tempfile.mkstemp(prefix=".timetable-", suffix=".csv", dir=output)
    os.close(descriptor)
    temp_csv = Path(temporary)
    descriptor, temporary_json = tempfile.mkstemp(prefix=".diagnostics-", suffix=".json", dir=output)
    os.close(descriptor)
    temp_json = Path(temporary_json)
    try:
        with temp_csv.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
            writer.writeheader()
            for course_id in ordered:
                course, value = problem.courses[course_id], assignment[course_id]
                slot = problem.slots[value.slot_id]
                writer.writerow({"course_id": course_id, "room_id": value.room_id, "slot_id": value.slot_id,
                                 "instructor_id": ";".join(course.instructors), "cohort_id": ";".join(course.cohorts),
                                 "day": slot.day, "start_time": slot.start_time, "end_time": slot.end_time})
        verification = validate_timetable(problem, temp_csv)
        if not verification["valid"]:
            raise RuntimeError("Export verification failed: " + "; ".join(verification["violations"]))
        source_names = ["courses.csv", "instructors.csv", "rooms.csv", "time_slots.csv", "student_cohorts.csv",
                        "constraints.csv", "sample_constraints.json", "scheduler_config.json"]
        digest = {name: hashlib.sha256((Path(data_dir) / name).read_bytes()).hexdigest()
                  for name in source_names if (Path(data_dir) / name).exists()}
        payload = {
            "student_id": student_id, "algorithm": algorithm, "python_version": platform.python_version(),
            "counts": {"courses": len(problem.courses), "rooms": len(problem.rooms),
                       "eligible_rooms": sum(r.available for r in problem.rooms.values()),
                       "time_slots": len(problem.slots), "instructors": len(problem.instructors), "cohorts": len(problem.cohorts)},
            "settings": settings, "assumptions": problem.assumptions, "warnings": problem.warnings,
            "normalized_courses": {c: asdict(v) for c, v in problem.courses.items()},
            "source_sha256": digest, "timetable_sha256": hashlib.sha256(temp_csv.read_bytes()).hexdigest(),
            "backtracking": csp.stats or None, "local_search": local_stats,
            "backtracking_penalties": initial_penalties, "final_penalties": engine.soft_penalties(assignment),
            "hard_violations": violations, "export_verification": {k: v for k, v in verification.items() if k != "penalties"},
            "elapsed_seconds": perf_counter() - started,
        }
        _write_json(temp_json, payload)
        os.replace(temp_json, diagnostics)
        os.replace(temp_csv, timetable)
    finally:
        temp_csv.unlink(missing_ok=True)
        temp_json.unlink(missing_ok=True)
    return str(timetable)


def studentscheduler(data_dir="data", output_dir="data", student_id=None):
    """Load current data, solve, optimize, export, and return the timetable CSV path."""
    return run_scheduler(data_dir, output_dir, student_id)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--output-dir", default="data")
    parser.add_argument("--student-id", default=None)
    parser.add_argument("--solver", choices=["hybrid", "backtracking", "min-conflicts"], default="hybrid")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--local-steps", type=int, default=None)
    args = parser.parse_args()
    try:
        path = run_scheduler(args.data_dir, args.output_dir, args.student_id,
                             algorithm=args.solver, seed=args.seed, local_steps=args.local_steps)
    except (DataError, NoSolutionError, SearchLimitError, ValueError, OSError) as exc:
        parser.exit(2, f"Scheduling failed: {exc}\n")
    result = json.loads(Path(path).with_name(Path(path).name.replace("_timetable.csv", "_diagnostics.json")).read_text())
    print(f"Timetable: {path}")
    print(f"Scheduled {result['counts']['courses']} courses; hard violations: {len(result['hard_violations'])}")
    if result["backtracking_penalties"] is not None:
        print(f"Soft penalty: {result['backtracking_penalties']['total']:.3f} -> {result['final_penalties']['total']:.3f}")
    else:
        print(f"Soft penalty: {result['final_penalties']['total']:.3f}")
    print(f"Elapsed: {result['elapsed_seconds']:.2f}s")
    if result["warnings"]:
        print(f"Input limitations: {len(result['warnings'])} warnings; read {Path(path).stem.replace('_timetable', '_diagnostics')}.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
