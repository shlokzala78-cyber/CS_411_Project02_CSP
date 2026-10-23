
from __future__ import annotations

import csv
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path


class DataError(ValueError):
    """An input file is missing, inconsistent, or unsupported."""


def tokens(value: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(v.strip() for v in re.split(r"[;|]", value or "") if v.strip()))


def minutes(value: str) -> int:
    if not re.fullmatch(r"\d{1,2}:\d{2}", value):
        raise DataError(f"Expected HH:MM time, got {value!r}")
    hour, minute = map(int, value.split(":"))
    if hour > 23 or minute > 59:
        raise DataError(f"Invalid clock time: {value!r}")
    return hour * 60 + minute


def integer(value, label: str, minimum: int = 0) -> int:
    try:
        n = int(str(value))
    except (TypeError, ValueError) as exc:
        raise DataError(f"{label} must be an integer, got {value!r}") from exc
    if n < minimum:
        raise DataError(f"{label} must be >= {minimum}")
    return n


def read_rows(directory: Path, name: str, required: set[str]) -> list[dict[str, str]]:
    path = directory / name
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)):
                raise DataError(f"{name}: empty or duplicate column headers")
            missing = required - set(reader.fieldnames)
            if missing:
                raise DataError(f"{name}: missing columns {sorted(missing)}")
            result = []
            for line, row in enumerate(reader, 2):
                if None in row or any(v is None for v in row.values()):
                    raise DataError(f"{name}:{line}: wrong number of CSV fields")
                if any(v.strip() for v in row.values()):
                    result.append({k: v.strip() for k, v in row.items()})
            return result
    except (OSError, UnicodeError, csv.Error) as exc:
        raise DataError(f"Cannot read {path}: {exc}") from exc


def index_rows(rows: list[dict[str, str]], key: str, name: str) -> dict[str, dict]:
    result = {}
    for row in rows:
        identifier = row[key]
        if not identifier or identifier in result:
            raise DataError(f"{name}: empty or duplicate {key} {identifier!r}")
        result[identifier] = row
    return result


def check_refs(items, valid, label):
    unknown = set(items) - set(valid)
    if unknown:
        raise DataError(f"{label}: unknown references {sorted(unknown)}")


def room_mentions(value: str) -> tuple[str, ...]:
    #Expand catalog shorthand such as TECH 201/202/203/204.
    output = []
    for token in tokens(value):
        match = re.fullmatch(r"(.+?\s)(\d+(?:/\d+)+)", token)
        output.extend([match[1] + number for number in match[2].split("/")] if match else [token])
    return tuple(dict.fromkeys(output))


@dataclass(frozen=True)
class Slot:
    id: str
    day: str
    start: int
    end: int
    start_time: str
    end_time: str

    def overlaps(self, other: "Slot") -> bool:
        return self.day == other.day and self.start < other.end and other.start < self.end


@dataclass(frozen=True)
class Room:
    id: str
    building: str
    capacity: int
    room_type: str
    features: str
    available: bool
    unavailable: tuple[str, ...] = ()


@dataclass(frozen=True)
class Instructor:
    id: str
    name: str
    preferred: tuple[str, ...] = ()
    unavailable: tuple[str, ...] = ()


@dataclass(frozen=True)
class Cohort:
    id: str
    size: int
    courses: tuple[str, ...]


@dataclass(frozen=True)
class Course:
    id: str
    enrollment: int
    instructors: tuple[str, ...]
    cohorts: tuple[str, ...]
    required_room_type: str = ""
    preferred_rooms: tuple[str, ...] = ()
    requested_rooms: tuple[str, ...] = ()
    activity: str = ""
    preferred_slots: tuple[str, ...] = ()
    unavailable_slots: tuple[str, ...] = ()


@dataclass
class Problem:
    courses: dict[str, Course]
    rooms: dict[str, Room]
    slots: dict[str, Slot]
    instructors: dict[str, Instructor]
    cohorts: dict[str, Cohort]
    settings: dict
    warnings: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)


DEFAULTS = {
    "default_enrollment": 30,
    "graduate_default_enrollment": 28,
    "strict_missing_data": False,
    "seed": 42,
    "local_steps": 150,
    "local_patience": 35,
    "local_candidate_limit": 100,
    "max_search_seconds": 60,
    "max_search_nodes": 200000,
    "normal_break_minutes": 10,
    "transit_minutes": 15,
    "unused_seat_allowance": 30,
    "weights": {"S1": 10, "S2": 5, "S3": 8, "S4": 12, "S5": 6,
                "room_request": 10, "historical_room": 2, "activity_fit": 5},
}


def load_settings(directory: Path) -> dict:
    settings = {**DEFAULTS, "weights": dict(DEFAULTS["weights"])}
    sample = directory / "sample_constraints.json"
    try:
        if sample.exists():
            payload = json.loads(sample.read_text(encoding="utf-8-sig"))
            for constraint in payload.get("soft_constraints", []):
                code = constraint["id"].split("_")[0]
                if code not in {"S1", "S2", "S3", "S4", "S5"}:
                    raise DataError(f"Unknown soft constraint in sample_constraints.json: {code}")
                settings["weights"][code] = constraint["penalty_weight"]
        path = directory / "scheduler_config.json"
        if path.exists():
            custom = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(custom, dict) or set(custom) - set(DEFAULTS):
                raise DataError("scheduler_config.json: unknown settings or non-object JSON")
            weights = custom.get("weights", {})
            if not isinstance(weights, dict) or set(weights) - set(DEFAULTS["weights"]):
                raise DataError("scheduler_config.json: unknown penalty weights")
            settings.update({k: v for k, v in custom.items() if k != "weights"})
            settings["weights"].update(weights)
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        if isinstance(exc, DataError):
            raise
        raise DataError(f"Invalid scheduler JSON: {exc}") from exc
    if type(settings["strict_missing_data"]) is not bool:
        raise DataError("strict_missing_data must be true or false")
    for key in ("default_enrollment", "graduate_default_enrollment", "seed", "local_steps",
                "normal_break_minutes", "transit_minutes", "unused_seat_allowance"):
        settings[key] = integer(settings[key], key)
    for key in ("local_patience", "local_candidate_limit"):
        settings[key] = integer(settings[key], key, 1)
    if settings["max_search_nodes"] is not None:
        settings["max_search_nodes"] = integer(settings["max_search_nodes"], "max_search_nodes", 1)
    for key, value in settings["weights"].items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise DataError(f"Weight {key} must be finite and nonnegative")
    limit = settings["max_search_seconds"]
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, (float, int))
                              or not math.isfinite(limit) or limit <= 0):
        raise DataError("max_search_seconds must be positive or null")
    return settings


def load_data(data_dir="data") -> Problem:
    directory = Path(data_dir)
    settings = load_settings(directory)
    warnings, assumptions = [], []
    tables = {}
    schemas = {
        "courses": {"course_id"}, "rooms": {"room_id", "room_type"},
        "time_slots": {"slot_id", "day", "start_time", "end_time"},
        "instructors": {"instructor_id", "instructor_name"},
        "student_cohorts": {"cohort_id", "size", "mandatory_courses"},
        "constraints": {"constraint_id", "constraint_type", "rule", "applies_to"},
    }
    keys = {"courses": "course_id", "rooms": "room_id", "time_slots": "slot_id",
            "instructors": "instructor_id", "student_cohorts": "cohort_id", "constraints": "constraint_id"}
    for name, schema in schemas.items():
        tables[name] = index_rows(read_rows(directory, name + ".csv", schema), keys[name], name)
    if not tables["courses"] or not tables["rooms"] or not tables["time_slots"]:
        raise DataError("Courses, rooms, and time slots must be nonempty")
    slots = {}
    weekdays = {d.lower(): d for d in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")}
    for identifier, row in tables["time_slots"].items():
        day = weekdays.get(row["day"].lower())
        if day is None:
            raise DataError(f"{identifier}: invalid day {row['day']!r}")
        start, end = minutes(row["start_time"]), minutes(row["end_time"])
        if end <= start:
            raise DataError(f"{identifier}: end_time must be after start_time on the same day")
        slots[identifier] = Slot(identifier, day, start, end, f"{start//60:02}:{start%60:02}", f"{end//60:02}:{end%60:02}")
    banned_rooms, requests = set(), {}
    supported_hard = {"enrollment <= scheduling_capacity", "no overlapping meetings in same room",
                      "no overlapping meetings for same instructor", "available_for_regular_classes = 'yes'"}
    supported_preferred = {"required_room_type matches room_type/features",
                           "activity_type compatible with room_type",
                           "minimize scheduling_capacity - enrollment"}
    for identifier, row in tables["constraints"].items():
        rule = row["rule"].strip()
        kind = row["constraint_type"].lower()
        if kind == "hard":
            match = re.fullmatch(r"room_id\s*!=\s*['\"](.+?)['\"]", rule)
            if match:
                check_refs([match[1]], tables["rooms"], identifier)
                banned_rooms.add(match[1])
            elif rule.lower() not in supported_hard:
                raise DataError(f"{identifier}: unsupported hard rule {rule!r}; implement it before scheduling")
        elif kind in {"preferred", "soft"}:
            match = re.fullmatch(r"preferred_room\s*=\s*['\"](.+?)['\"]", rule)
            if match:
                targets = tokens(row["applies_to"])
                check_refs(targets, tables["courses"], identifier)
                check_refs([match[1]], tables["rooms"], identifier)
                for course in targets:
                    requests.setdefault(course, []).append(match[1])
            elif rule.lower() not in supported_preferred:
                warnings.append(f"{identifier}: preference not directly scored ({rule}); no historical timetable or section enrollment is supplied.")
        else:
            raise DataError(f"{identifier}: unknown constraint_type {row['constraint_type']!r}")
    rooms = {}
    for identifier, row in tables["rooms"].items():
        capacity_text = row.get("scheduling_capacity", "") or row.get("capacity", "")
        capacity = integer(capacity_text, f"{identifier} capacity")
        if row.get("physical_capacity") and capacity > integer(row["physical_capacity"], f"{identifier} physical capacity"):
            raise DataError(f"{identifier}: scheduling capacity exceeds physical capacity")
        availability = row.get("available_for_regular_classes", "Yes").lower()
        if availability not in {"yes", "no", "true", "false", "1", "0"}:
            raise DataError(f"{identifier}: invalid room availability")
        unavailable = tokens(row.get("unavailable_slots", ""))
        check_refs(unavailable, slots, f"{identifier} unavailable_slots")
        rooms[identifier] = Room(identifier, row.get("building") or identifier.split()[0], capacity,
                                 row["room_type"], row.get("features_or_constraints", ""),
                                 availability in {"yes", "true", "1"} and capacity > 0 and identifier not in banned_rooms,
                                 unavailable)
    instructors, names, reverse = {}, {}, {}
    for identifier, row in tables["instructors"].items():
        preferred = tokens(row.get("preferred_slots", ""))
        unavailable = tokens(row.get("unavailable_slots", ""))
        check_refs(preferred + unavailable, slots, identifier)
        instructors[identifier] = Instructor(identifier, row["instructor_name"], preferred, unavailable)
        names.setdefault(row["instructor_name"].casefold(), []).append(identifier)
        linked = tokens(row.get("courses", ""))
        check_refs(linked, tables["courses"], identifier)
        for course in linked:
            reverse.setdefault(course, []).append(identifier)
    cohorts = {}
    for identifier, row in tables["student_cohorts"].items():
        linked = tokens(row["mandatory_courses"])
        check_refs(linked, tables["courses"], identifier)
        cohorts[identifier] = Cohort(identifier, integer(row["size"], f"{identifier} size"), linked)
    courses = {}
    inferred, missing_teachers = [], []
    for identifier, row in tables["courses"].items():
        teachers = list(reverse.get(identifier, []))
        for teacher in tokens(row.get("instructor_id", "") or row.get("instructor_ids", "") or row.get("instructors", "")):
            if teacher in instructors:
                teachers.append(teacher)
            elif len(names.get(teacher.casefold(), [])) == 1:
                teachers.append(names[teacher.casefold()][0])
            else:
                raise DataError(f"{identifier}: unknown or ambiguous instructor {teacher!r}")
        teachers = tuple(sorted(set(teachers)))
        memberships = set(tokens(row.get("cohort_id", "") or row.get("cohort_ids", "")))
        check_refs(memberships, cohorts, identifier)
        memberships.update(k for k, v in cohorts.items() if identifier in v.courses)
        memberships = tuple(sorted(memberships))
        cohort_size = sum(cohorts[k].size for k in memberships)
        if row.get("enrollment"):
            enrollment = integer(row["enrollment"], f"{identifier} enrollment")
            if enrollment < cohort_size:
                raise DataError(f"{identifier}: enrollment {enrollment} is smaller than mandatory cohort total {cohort_size}")
        else:
            if settings["strict_missing_data"]:
                raise DataError(f"{identifier}: enrollment missing in strict mode")
            default = settings["graduate_default_enrollment"] if row.get("level", "").lower() == "graduate" else settings["default_enrollment"]
            enrollment = cohort_size if memberships else default
            inferred.append(f"{identifier}={enrollment}")
        if not teachers:
            if settings["strict_missing_data"]:
                raise DataError(f"{identifier}: instructor missing in strict mode")
            missing_teachers.append(identifier)
        mentions = room_mentions(row.get("rooms_mentioned", ""))
        unknown = set(mentions) - set(rooms)
        if unknown:
            warnings.append(f"{identifier}: unavailable historical room references ignored: {sorted(unknown)}")
        requested = tuple(dict.fromkeys(requests.get(identifier, []) + list(tokens(row.get("preferred_room", "")))))
        check_refs(requested, rooms, identifier)
        preferred = tokens(row.get("preferred_slots", ""))
        unavailable = tokens(row.get("unavailable_slots", ""))
        check_refs(preferred + unavailable, slots, identifier)
        courses[identifier] = Course(identifier, enrollment, teachers, memberships,
                                      row.get("required_room_type", ""), tuple(k for k in mentions if k in rooms),
                                      requested, row.get("activity_type", "") or row.get("scheduling_context", ""),
                                      preferred, unavailable)
    assumptions.extend([
        "One course row is one meeting per week; the source does not define section counts or weekly contact hours.",
        "Multiple listed instructors are treated conservatively as co-teachers; all must be free.",
        "Mandatory cohorts are assumed disjoint; their sizes are summed for courses shared by cohorts.",
        "Historical room mentions and activity text are preferences; only required_room_type is a hard type requirement.",
        "Missing availability means unrestricted availability, not independently verified availability.",
    ])
    if inferred:
        assumptions.append("Missing enrollment inferred from mandatory cohort totals, otherwise configured undergraduate/graduate defaults: " + "; ".join(inferred))
    if missing_teachers:
        warnings.append("No instructor assigned for " + "; ".join(missing_teachers) + ". Output instructor_id is blank; clashes for unknown instructors cannot be checked.")
    if not any(i.preferred or i.unavailable for i in instructors.values()):
        warnings.append("No instructor preference or unavailability slots were supplied; S1 is zero unless course preferred_slots are supplied.")
    return Problem(courses, rooms, slots, instructors, cohorts, settings, warnings, assumptions)
