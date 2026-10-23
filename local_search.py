
from __future__ import annotations

import random
from time import perf_counter

from backtracking_solver import BacktrackingSolver, NoSolutionError, SearchLimitError, bits


class MinConflictsSolver:
    def __init__(self, csp: BacktrackingSolver, *, seed=42, max_steps=150,
                 patience=35, candidate_limit=100):
        if max_steps < 0 or patience < 1 or candidate_limit < 1:
            raise ValueError("Invalid local search budget")
        self.csp, self.engine = csp, csp.engine
        self.seed, self.max_steps = seed, max_steps
        self.patience, self.candidate_limit = patience, candidate_limit
        self.stats = {}

    def _conflicts(self, course, value, assignment):
        return sum(self.engine.conflicts(course, value, other, old)
                   for other, old in assignment.items() if other != course)

    def optimize(self, initial=None):
        started = perf_counter()
        rng = random.Random(self.seed)
        domains = {c: list(bits(d)) for c, d in self.csp.initial_domains.items()}
        if any(not d for d in domains.values()):
            raise NoSolutionError("Local search cannot repair an empty domain")
        if initial is None:
            assignment = {c: self.csp.values[rng.choice(d)] for c, d in domains.items()}
        else:
            if set(initial) != set(domains):
                raise ValueError("Min-conflicts needs a complete course assignment")
            if any(self.engine.unary_violations(c, v) for c, v in initial.items()):
                raise ValueError("Initial placements must satisfy unary constraints")
            assignment = dict(initial)
        initial_errors = self.engine.hard_violations(assignment)
        initial_score = self.engine.soft_penalties(assignment)["total"]
        best = None if initial_errors else dict(assignment)
        best_score = float("inf") if best is None else initial_score
        stalled = 0
        self.stats = {"seed": self.seed, "steps": 0, "accepted_moves": 0, "improvements": 0,
                      "initial_hard_violations": len(initial_errors), "initial_soft_penalty": initial_score,
                      "candidate_limit": self.candidate_limit, "history": [best_score] if best is not None else []}
        for step in range(self.max_steps):
            self.stats["steps"] = step + 1
            conflicted = [c for c in sorted(assignment) if self._conflicts(c, assignment[c], assignment)]
            repairing = bool(conflicted)
            scores = self.engine.soft_penalties(assignment)
            if repairing:
                course = rng.choice(conflicted)
            else:
                contributors = scores["contributors"]
                expensive = [c for c in sorted(assignment) if contributors[c] > 1e-9]
                if not expensive or best_score <= 1e-9:
                    break
                course = rng.choices(expensive, weights=[contributors[c] for c in expensive], k=1)[0]
            old = assignment[course]
            indices = domains[course]
            # A bounded, reproducible neighborhood; no global-optimality claim.
            candidates = rng.sample(indices, self.candidate_limit) if len(indices) > self.candidate_limit else list(indices)
            old_conflicts = self._conflicts(course, old, assignment)
            candidate_score = (old_conflicts, scores["total"])
            options = [old]
            for index in candidates:
                value = self.csp.values[index]
                if value == old:
                    continue
                conflicts = self._conflicts(course, value, assignment)
                if (not repairing and conflicts) or conflicts > candidate_score[0]:
                    continue
                assignment[course] = value
                objective = (conflicts, self.engine.soft_penalties(assignment)["total"])
                if objective < candidate_score:
                    candidate_score, options = objective, [value]
                elif objective == candidate_score:
                    options.append(value)
            chosen = rng.choice(options)
            assignment[course] = chosen
            if chosen != old:
                self.stats["accepted_moves"] += 1
            feasible = not self.engine.hard_violations(assignment)
            current_score = self.engine.soft_penalties(assignment)["total"]
            if feasible and current_score < best_score - 1e-9:
                best, best_score = dict(assignment), current_score
                self.stats["improvements"] += 1
                self.stats["history"].append(best_score)
                stalled = 0
            else:
                stalled += 1
            if stalled >= self.patience and not repairing:
                break
        self.stats["elapsed_seconds"] = perf_counter() - started
        if best is None:
            raise SearchLimitError("Min-conflicts did not find a feasible schedule within its budget; this does not prove infeasibility")
        self.stats["final_soft_penalty"] = best_score
        self.stats["final_hard_violations"] = len(self.engine.hard_violations(best))
        return best
