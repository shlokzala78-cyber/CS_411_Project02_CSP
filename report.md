# Project 2 Report: University Class Scheduling & Timetable Optimization

> [!IMPORTANT]
> **AUTOGRADER COMPLIANCE INSTRUCTIONS:**
> This report is parsed automatically by the autograder. To ensure you receive full credit for your work:
> 1. **Do not modify** the section headers (`## ...`) or bold field keys (e.g., `**Name:**`, `**Total Courses Configured:**`, `**Video Presentation Link:**`, etc.).
> 2. **Write your answers directly after** the colon `:` of each field, replacing the placeholder text completely (including the outer brackets `[` and `]`).
> 3. **Maintain the file structure**. Changing headers, bold titles, or deleting lines can cause the autograder to miss your responses and award 0 marks.

---

## Student Information 
- **Name:** Shlok Zala
- **UID (netID):** szala
- **UIN:** 659741534

---

## Section 1: Dataset & University Configuration
- **Total Courses Configured:** 65
- **Total Rooms Configured:** 20
- **Total Time Slots Configured:** 50

---

## Section 2: Local Verification & Implemented Solvers
*Check the algorithms and features you successfully ran and verified by placing an `x` in the brackets (e.g., `[x]`):*
- [x] DataLoader & CSV Parsing
- [x] Hard Constraint Validation Engine
- [x] Soft Penalty & Quality Evaluation Engine
- [x] CSP Backtracking with MRV + LCV + Forward Checking
- [x] Min-Conflicts CSP Local Search
- [x] Timetable CSV Export (szala_timetable.csv)

---

## Section 3: Video Presentation & Project Structure
- **Video Presentation Link:** https://drive.google.com/file/d/1UgDYLmj8hlAtHrTysRwszM1llpwhRnDW/view?usp=sharing
- **Project Directory Structure:** Plain Python modules in the project root with app.py as the entry point and data/ for the inputs and generated outputs.

---

## Section 4: Discussion & Problem Formulation
*Provide your written analysis for each point by replacing the bracket placeholders below:*
- **How did you formulate the variables, domains, and constraints for this scheduling problem?**
    There are 65 courses, which represent the variables. The domain of each course includes (room, time slot) pairs that are allowed. Rooms should be available,large enough, and meet all conditions on room type. LAB 254 is excluded because it is the testing center. Courses in courses.csv do not include enrollments; therefore, it will be calculated based on the size of the mandatory cohorts, or it will default to 30 students for undergraduate courses and 28 students for graduate courses without any mandatory cohorts. Hard constraints are used to avoid room, instructor, or mandatory cohort overlap. There is no room, instructor, or mandatory cohort overlap based on their real starting and ending times. Instructors with unknown names stay unassigned, thus their conflicts cannot be evaluated. Soft constraints evaluate instructor preferences, unoccupied seats, empty seats among students, consecutive courses, travel between buildings, room preferences, historical rooms, and activity appropriateness. Weights S1-S5 are defined in sample_constraints.json; others have default configurable weights.
- **Compare CSP Backtracking (with heuristics) vs Local Search (Min-Conflicts) in terms of scalability, completeness, and solution quality.**
    Backtracking becomes complete if we allow it to perform unbounded searching, but the time it takes to complete can grow fast for large or tightly constrained instances. Our implementation of the algorithm allows us to impose the bounds on the computation time and number of explored nodes, so hitting either bound does not imply that there is no solution. Using MRV, the degree heuristic, LCV, and forward checking helps us avoid useless searches. On the provided dataset, the backtracking approach spent 65 nodes and did zero backtracks, thus generating a possible timetable with a soft penalty of 283. This algorithm is feasibility-driven, and individual soft penalties are used as the tie-breaker for value ordering. Min-Conflicts performs a single assignment per step and is able to solve large problems fast, but is incomplete and can get stuck. Local optimization decreases the penalty from 283 to 121 while maintaining the hard feasibility of the solution.
- **How can modern AI / LLM techniques be combined with CSP solvers for real-world automated university scheduling?**
    An LLM could translate emails and scheduling remarks like “prefers 1 class a day” into structured suggestions of constraints. A person must evaluate the proposed interpretation in terms of whether it’s necessary or recommended to pass on to the solver. Then, a CSP solver could enforce the validated constraints and optimize the recommendations. Anyway, it will check only those constraints it was provided with, so it can’t identify all the requirements that the LLM might misinterpret or overlook. An LLM could also provide explanations for scheduling decisions through verified solver outputs and convert requests for changes into suggested modifications.
