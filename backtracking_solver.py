
from __future__ import annotations

from collections import defaultdict
from time import perf_counter

from constraints_check import ConstraintEngine, Placement


class NoSolutionError(RuntimeError):
    """Hard constraints admit no complete assignment."""


class SearchLimitError(RuntimeError):
    """Search was stopped; feasibility is still unknown."""


def bits(mask: int):
    while mask:
        bit = mask & -mask
        yield bit.bit_length() - 1
        mask ^= bit


class BacktrackingSolver:
    def __init__(self, engine: ConstraintEngine, *, max_nodes=None, max_seconds=None):
        self.engine = engine
        self.max_nodes, self.max_seconds = max_nodes, max_seconds
        p = engine.problem
        self.values = tuple(Placement(r, s) for r in p.rooms if p.rooms[r].available for s in p.slots)
        room_bits, slot_bits = defaultdict(int), defaultdict(int)
        for index, value in enumerate(self.values):
            room_bits[value.room_id] |= 1 << index
            slot_bits[value.slot_id] |= 1 << index
        overlap_bits = {s: sum(slot_bits[t] for t in engine.overlap[s]) for s in p.slots}
        self.time_blocks = tuple(overlap_bits[v.slot_id] for v in self.values)
        self.room_blocks = tuple(overlap_bits[v.slot_id] & room_bits[v.room_id] for v in self.values)
        self.initial_domains = {}
        self.unary_costs = {}
        for course in p.courses:
            mask = 0
            costs = {}
            for index, value in enumerate(self.values):
                if not engine.unary_violations(course, value):
                    mask |= 1 << index
                    costs[index] = engine.unary_cost(course, value)
            self.initial_domains[course] = mask
            self.unary_costs[course] = costs
        possible_rooms = {c: {self.values[i].room_id for i in bits(d)} for c, d in self.initial_domains.items()}
        self.neighbors = {c: frozenset(d for d in p.courses if c != d and (
            d in engine.shared[c] or possible_rooms[c] & possible_rooms[d])) for c in p.courses}
        self.stats = {}

    def blocked(self, course: str, index: int, neighbor: str) -> int:
        return self.time_blocks[index] if neighbor in self.engine.shared[course] else self.room_blocks[index]

    def _check_limit(self, check_nodes=True):
        if check_nodes and self.max_nodes is not None and self.stats["nodes"] >= self.max_nodes:
            raise SearchLimitError(f"Reached node limit ({self.max_nodes}); feasibility is unknown")
        if self.max_seconds is not None and perf_counter() - self.started >= self.max_seconds:
            raise SearchLimitError(f"Reached time limit ({self.max_seconds}s); feasibility is unknown")

    def select_variable(self, domains):
        # MRV first, then most constraints on unassigned variables, then stable ID.
        return min(domains, key=lambda c: (domains[c].bit_count(),
                   -sum(n in domains for n in self.neighbors[c]), c))

    def order_values(self, course, domains):
        neighbors = sorted(self.neighbors[course] & domains.keys())
        scores = []
        for count, index in enumerate(bits(domains[course])):
            if count % 128 == 0:
                self._check_limit(check_nodes=False)
            eliminated = sum((domains[n] & self.blocked(course, index, n)).bit_count() for n in neighbors)
            scores.append((eliminated, self.unary_costs[course][index], index))
        return [index for _, _, index in sorted(scores)]

    def _search(self, assignment, domains):
        if not domains:
            return assignment.copy()
        self._check_limit()
        self.stats["nodes"] += 1
        course = self.select_variable(domains)
        for index in self.order_values(course, domains):
            self.stats["values_tried"] += 1
            next_domains = domains.copy()
            del next_domains[course]
            feasible = True
            for neighbor in sorted(self.neighbors[course] & next_domains.keys()):
                old = next_domains[neighbor]
                new = old & ~self.blocked(course, index, neighbor)
                self.stats["values_pruned"] += old.bit_count() - new.bit_count()
                if not new:
                    self.stats["domain_wipeouts"] += 1
                    feasible = False
                    break
                next_domains[neighbor] = new
            if feasible:
                assignment[course] = index
                result = self._search(assignment, next_domains)
                if result is not None:
                    return result
                del assignment[course]
        self.stats["backtracks"] += 1
        return None

    def solve(self):
        self.stats = {"nodes": 0, "values_tried": 0, "values_pruned": 0, "domain_wipeouts": 0, "backtracks": 0}
        self.started = perf_counter()
        try:
            empty = [c for c, d in self.initial_domains.items() if not d]
            if empty:
                raise NoSolutionError("No eligible room/time assignments for: " + ", ".join(empty))
            result = self._search({}, self.initial_domains.copy())
            if result is None:
                raise NoSolutionError("Exhaustive backtracking found no feasible timetable")
            assignment = {c: self.values[i] for c, i in result.items()}
            errors = self.engine.hard_violations(assignment)
            if errors:
                raise RuntimeError("Internal solver invariant failed: " + "; ".join(errors))
            return assignment
        finally:
            self.stats["elapsed_seconds"] = perf_counter() - self.started
