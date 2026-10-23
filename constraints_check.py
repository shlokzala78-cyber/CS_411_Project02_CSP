
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from dataclasses import dataclass
from itertools import combinations

from data_loader import DataError, Problem, load_data, tokens


@dataclass(frozen=True)
class Placement:
    room_id: str
    slot_id: str


def type_matches(required: str, room_type: str, features: str = "") -> bool:
    """Semicolon/pipe alternatives; explicit computer labs require computer capability."""
    if not required:
        return True
    kind = room_type.lower().replace("-", " ")
    equipment = features.lower()
    for alternative in tokens(required):
        need = alternative.lower().replace("_", " ").replace("-", " ").strip()
        if need in {"any", "*"}:
            return True
        if need in {"computer lab", "computer laboratory"}:
            match = "computer lab" in kind or "workstation" in equipment or "computer lab" in equipment
        elif need in {"lab", "laboratory"}:
            match = "lab" in kind or "workstation" in equipment
        elif need in {"lecture", "lecture hall", "auditorium"}:
            match = "lecture" in kind or "auditorium" in kind
        elif need in {"classroom", "seminar"}:
            match = "classroom" in kind or "seminar" in kind
        elif need in {"specialized", "evl"}:
            match = "specialized" in kind or "evl" in kind or "evl" in equipment
        else:
            match = need == kind
        if match:
            return True
    return False


class ConstraintEngine:
    def __init__(self, problem: Problem):
        self.problem = problem
        self.overlap = {s: frozenset(t for t in problem.slots if problem.slots[s].overlaps(problem.slots[t]))
                        for s in problem.slots}
        self.shared = {c: frozenset(d for d in problem.courses if c != d and (
            set(problem.courses[c].instructors) & set(problem.courses[d].instructors)
            or set(problem.courses[c].cohorts) & set(problem.courses[d].cohorts))) for c in problem.courses}

    def unary_violations(self, course_id: str, value: Placement) -> list[str]:
        p = self.problem
        if course_id not in p.courses:
            return [f"H6: unknown course {course_id}"]
        if not isinstance(value, Placement) or value.room_id not in p.rooms or value.slot_id not in p.slots:
            return [f"H6: {course_id} has an unknown room or time slot"]
        course, room = p.courses[course_id], p.rooms[value.room_id]
        violations = []
        if not room.available:
            violations.append(f"H2: {course_id} assigned unavailable room {room.id}")
        if course.enrollment > room.capacity:
            violations.append(f"H2: {course_id} enrollment {course.enrollment} exceeds {room.id} capacity {room.capacity}")
        if not type_matches(course.required_room_type, room.room_type, room.features):
            violations.append(f"H2: {course_id} requires {course.required_room_type}, got {room.room_type}")
        overlapping = self.overlap[value.slot_id]
        if overlapping.intersection(room.unavailable):
            violations.append(f"H2: {course_id} overlaps {room.id} room unavailability")
        if overlapping.intersection(course.unavailable_slots):
            violations.append(f"H4: {course_id} overlaps course unavailability")
        for teacher in course.instructors:
            if overlapping.intersection(p.instructors[teacher].unavailable):
                violations.append(f"H4: {course_id} overlaps {teacher} unavailability")
        return violations

    def conflicts(self, first: str, a: Placement, second: str, b: Placement) -> bool:
        return b.slot_id in self.overlap[a.slot_id] and (a.room_id == b.room_id or second in self.shared[first])

    def hard_violations(self, assignment: dict[str, Placement], complete=True) -> list[str]:
        violations = []
        if complete:
            for course in sorted(set(self.problem.courses) - set(assignment)):
                violations.append(f"H6: missing course {course}")
        valid = {}
        for course, value in assignment.items():
            violations.extend(self.unary_violations(course, value))
            if course in self.problem.courses and isinstance(value, Placement) and value.room_id in self.problem.rooms and value.slot_id in self.problem.slots:
                valid[course] = value
        for (first, a), (second, b) in combinations(valid.items(), 2):
            if b.slot_id not in self.overlap[a.slot_id]:
                continue
            ca, cb = self.problem.courses[first], self.problem.courses[second]
            if a.room_id == b.room_id:
                violations.append(f"H1: {first} and {second} overlap in {a.room_id}")
            teachers = set(ca.instructors) & set(cb.instructors)
            if teachers:
                violations.append(f"H3: {first} and {second} overlap for instructors {sorted(teachers)}")
            cohorts = set(ca.cohorts) & set(cb.cohorts)
            if cohorts:
                violations.append(f"H5: {first} and {second} overlap for cohorts {sorted(cohorts)}")
        return violations

    def unary_penalties(self, course_id: str, value: Placement) -> dict[str, float]:
        p = self.problem
        course, room = p.courses[course_id], p.rooms[value.room_id]
        s1 = int(bool(course.preferred_slots) and value.slot_id not in course.preferred_slots)
        s1 += sum(bool(p.instructors[t].preferred) and value.slot_id not in p.instructors[t].preferred for t in course.instructors)
        activity = course.activity.lower()
        # Mixed catalog activities are ambiguous; only unambiguous activity hints are scored.
        hint = "computer lab" if ("lab" in activity and "lecture" not in activity and "discussion" not in activity) else ""
        if not hint and ("specialized" in activity or "evl" in activity):
            hint = "specialized"
        return {
            "S1": s1,
            "S2": max(0, room.capacity - course.enrollment - p.settings["unused_seat_allowance"]),
            "room_request": int(bool(course.requested_rooms) and room.id not in course.requested_rooms),
            "historical_room": int(bool(course.preferred_rooms) and room.id not in course.preferred_rooms),
            "activity_fit": int(bool(hint) and not type_matches(hint, room.room_type, room.features)),
        }

    def unary_cost(self, course_id: str, value: Placement) -> float:
        return sum(n * self.problem.settings["weights"][key] for key, n in self.unary_penalties(course_id, value).items())

    def soft_penalties(self, assignment: dict[str, Placement]) -> dict:
        #Report raw units, weighted terms, total, and affected-course contributions.

        p, weights = self.problem, self.problem.settings["weights"]
        raw = {key: 0.0 for key in weights}
        contributors = {course: 0.0 for course in assignment}

        def add(key, amount, affected):
            raw[key] += amount
            cost = amount * weights[key]
            for course in affected:
                contributors[course] += cost / len(affected)

        groups = defaultdict(list)
        for course_id, value in assignment.items():
            for key, count in self.unary_penalties(course_id, value).items():
                add(key, count, (course_id,))
            course, slot = p.courses[course_id], p.slots[value.slot_id]
            for cohort in course.cohorts:
                groups[("cohort", cohort, slot.day)].append((slot.start, slot.end, course_id, value.room_id))
            for teacher in course.instructors:
                groups[("instructor", teacher, slot.day)].append((slot.start, slot.end, course_id, value.room_id))
        for (kind, _, _), meetings in groups.items():
            meetings.sort()
            run = 1
            for previous, current in zip(meetings, meetings[1:]):
                gap = current[0] - previous[1]
                pair = (previous[2], current[2])
                if 0 <= gap <= p.settings["normal_break_minutes"]:
                    run += 1
                    if run > 2:
                        add("S4", 1, pair)
                else:
                    run = 1
                if kind == "cohort":
                    add("S3", max(0, gap - p.settings["normal_break_minutes"]) / 60, pair)
                    if 0 <= gap < p.settings["transit_minutes"] and p.rooms[previous[3]].building != p.rooms[current[3]].building:
                        add("S5", 1, pair)
        weighted = {key: raw[key] * weights[key] for key in raw}
        return {"raw": raw, "weighted": weighted, "total": sum(weighted.values()), "contributors": contributors}


CSV_FIELDS = ["course_id", "room_id", "slot_id", "instructor_id", "cohort_id", "day", "start_time", "end_time"]


def validate_timetable(problem: Problem, path) -> dict:
    """Re-read exported bytes; never trust a solver's validity claim or time labels."""
    errors, assignment = [], {}
    try:
        with open(path, encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)) or not set(CSV_FIELDS) <= set(reader.fieldnames):
                raise DataError("Timetable is missing required columns or contains duplicate headers")
            for line, row in enumerate(reader, 2):
                if None in row or any(v is None for v in row.values()):
                    errors.append(f"Line {line}: malformed CSV row")
                    continue
                course_id = row["course_id"]
                if course_id in assignment:
                    errors.append(f"H6: duplicate course {course_id}")
                value = Placement(row["room_id"], row["slot_id"])
                assignment[course_id] = value
                if course_id in problem.courses:
                    course = problem.courses[course_id]
                    if set(tokens(row["instructor_id"])) != set(course.instructors):
                        errors.append(f"{course_id}: instructor_id does not match input")
                    if set(tokens(row["cohort_id"])) != set(course.cohorts):
                        errors.append(f"{course_id}: cohort_id does not match input")
                if value.slot_id in problem.slots:
                    slot = problem.slots[value.slot_id]
                    if (row["day"], row["start_time"], row["end_time"]) != (slot.day, slot.start_time, slot.end_time):
                        errors.append(f"{course_id}: day/time labels do not match slot_id")
    except (OSError, UnicodeError, csv.Error) as exc:
        raise DataError(f"Cannot read timetable: {exc}") from exc
    engine = ConstraintEngine(problem)
    errors.extend(engine.hard_violations(assignment))
    return {"valid": not errors, "course_count": len(assignment), "violations": errors,
            "penalties": engine.soft_penalties(assignment) if not errors else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--timetable", required=True)
    args = parser.parse_args()
    try:
        result = validate_timetable(load_data(args.data_dir), args.timetable)
    except DataError as exc:
        parser.exit(2, f"Validation error: {exc}\n")
    print(json.dumps(result, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
