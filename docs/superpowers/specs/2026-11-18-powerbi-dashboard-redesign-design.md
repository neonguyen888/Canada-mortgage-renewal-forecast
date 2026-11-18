# Power BI dashboard redesign

Date: 2026-11-18. Owner: Neo Nguyen.

## Why

The Power BI report in `powerbi/` is part of Neo's portfolio. It will be read by two groups:

- recruiters and HR or hiring managers, who may know little about mortgages, and
- "customers": the business reader the analysis is for, such as a lender's renewal team.

Neo wants both groups to come away thinking two things: he understands the data, and he can
design a report so that a non-expert understands it quickly. The current report works and its
numbers are right, but it uses a dark navy theme that doesn't match the light, finance-report
style Neo has just given the charts in `reports/figures/` (commit `7d51660`), and it doesn't
explain itself to someone who opens the file cold.

## Goals

1. A light, professional finance look that matches `reports/figures/` (direction "B", chosen by
   Neo from three mockups): light grey canvas, white panels with thin borders, and a navy
   navigation rail down the left side.
2. Each analysis page opens with a headline that states the finding in plain English and is
   computed from the data, so it stays true when the data updates. Example for Overview:
   "92.5% of renewing borrowers face a rise above $250 a month".
3. KPI cards show context, not just a number: a change against last month, against a baseline or
   against a threshold, with a ▲/▼ marker where it makes sense (finance convention: variances
   first).
4. A fifth page, "About this project", at the end of the navigation: the business problem, who
   the report is for, data sources and freshness, methods, how to read the colours, tools used,
   and the author with a link to the GitHub repository.
5. Everything the four pages do today still works: the same questions, the same slicers and the
   same reference values (listed in `powerbi/README.md`).
6. Screenshots and both READMEs updated, then committed and pushed.
7. This design document and the implementation plan are committed to the public repository
   under `docs/superpowers/` (Neo's decision: they show the design process).

## Non-goals

- No change to the analysis: the Python pipeline, SQL, CSVs and the logic of existing measures
  stay as they are. New measures may be added for headlines and deltas.
- No custom or paid visuals. Native Power BI visuals only, so the file opens anywhere for free.
- No bookmarks or show/hide animations (hamburger menus and the like).
- No publishing to the Power BI Service.

## Anti-goals (would make this a failure even if it technically works)

- Decoration that adds nothing: gradients, icons for their own sake, 3D, busy backgrounds.
  Portfolio reviewers penalise "flashy over useful".
- Meaning carried by colour alone. The palette must stay colour-blind safe, and amber "attention"
  marks need a second cue (label or marker).
- Any clipped text, scrollbar or slicer overflow arrow in the finished pages.
- Any number that disagrees with the written report or the reference values.
- Breaking `powerbi/build_pbip.py` as the single source of the report.

## Constraints

- Page size 1280 x 720, `FitToPage`. Files follow Microsoft's PBIR and TMDL schemas; Power BI
  Desktop 2025 or later.
- `build_pbip.py` writes every file in `powerbi/`, including one background PNG per page drawn
  with matplotlib. The pipeline runs it, so changes made only in Power BI Desktop are lost.
- `tests/test_powerbi.py` must keep passing: no measure/column name clash, every visual field
  exists, unique visual names per page, every visual inside the page, slicer defaults are real
  options. All other tests must keep passing too.
- This Windows machine has no Python. Building needs Python 3 installed for the current user
  (no admin rights) and the packages listed in `run_pipeline.sh`.
- Power BI Desktop behaviour found while fixing the last version:
  - text boxes pad their text, so a box sized to the font clips it and shows a scrollbar
    (`label_height()` guards this);
  - a tile slicer won't shrink a tile below about 66 px, so a narrow strip needs dropdowns;
  - card visuals default to "Auto" display units, which turns $2,059.72 into $2.06K;
  - in a table, if any field is marked `active`, every field must be, or the others vanish.
- Opening the rebuilt project shows empty visuals until someone presses Refresh (the data cache
  isn't in Git, and `build_pbip.py` deletes the SemanticModel folder, cache included, on every
  rebuild). Claude can't click inside Power BI on this machine, so Neo presses Refresh and saves
  once after the first rebuild (the cache also still holds August data). After that, Claude's
  call: keep a copy of `.pbi/cache.abf` and put it back after each rebuild, which worked last
  time for measure-only changes. A rebuild that adds or changes tables needs Neo's Refresh again.
- Checking in Desktop (Claude's call, as last time): Claude opens and closes Power BI from the
  shell, switches pages by setting `activePageName` in `pages.json` before each open, and captures
  the Power BI window to check every page and to take the screenshots. Expect more than one
  rebuild-and-check round. Claude pushes once all five pages pass, since Neo asked for everything
  to be updated, and shows him the screenshots.
- Python (Claude's call; covered by approving this document): install Python 3 for the current
  user with winget, create `.venv` in the repo (Windows layout, `.venv\Scripts\python.exe`), and
  install the packages from `run_pipeline.sh`. `.venv/` is already ignored by Git.
- Commits are authored by Neo Nguyen with his GitHub noreply address and no Co-Authored-By line
  (Neo's standing instruction).

## Approaches considered

1. **Change `build_pbip.py` and rebuild (chosen).** Keeps one source of truth, keeps the
   pipeline and tests meaningful, and is the only way to redraw the background images. Needs a
   local Python install.
2. Edit in Power BI Desktop by hand. Lost on the next pipeline run, and there's no way to drive
   Desktop from this machine. Rejected.
3. Patch the generated JSON directly and mirror changes in the script without running it. Too
   easy for script and files to drift during a full redesign, and backgrounds still need
   matplotlib. Rejected.

Neo handed the choice of approach to Claude; option 1 is Claude's call.

## Design decided

### Look (Neo chose direction B)

- Canvas light grey (around `#F4F6F9`), panels white with a 1 px border in the report's rule
  colour (`#D5DAE0`) and a small corner radius. Ink `#1B1F24`, muted text `#5B6670`.
- Data colours from `src/charts.py`, which already passed a colour-blind check:
  navy `#2F5F96` actual data, teal `#16917F` ETS forecast, amber `#C26B12` attention (above the
  threshold, chosen bar), purple `#6E52A3` bond-yield model, grey `#A7B0BA` other comparisons.
  Negative/adverse deltas may use a muted red; favourable ones teal.
- The mockup Neo picked was shown in chat and isn't saved as a file. Its proportions, which the
  layout should follow: rail about one seventh of the page width (about 170 px of 1280), headline
  and subtitle across the top of the content area, a full-width row of four KPI cards, then a
  wide chart on the left (about 3/5) and a narrower one on the right (about 2/5). Where today's
  layout puts a slicer in the header (Triage capacity) or inside a panel (Overview scenario),
  the builder places it where it reads best in this grid (Claude's call).
- Navigation rail on the left in dark navy (around `#1F3A5F`), full page height, holding: a short
  report name, a native Power BI **page navigator** (vertical, one button per page, selected page
  highlighted), and the "data to" month at the bottom. The navigator is native so it updates when
  pages change and works in Desktop and the Service.
- Headline at the top of each page: bold, around 20 pt, ink colour, with a small navy rule above
  it (same device as the report figures) and a one-line grey subtitle with scope and units.
- KPI row of four white cards under the headline. Each card: label, value, and a context line.
- Footer on every page: source on the left, "Neo Nguyen" on the right.

### Pages

Page order in the navigator: Overview, Rates & accuracy, Payment impact, Renewal triage, About.
Overview is the page that opens.

1. **Overview.** Headline: share of renewing loans above the $250 threshold in the chosen
   scenario. KPIs: loans above +$250 (with share of book), total payment increase a month (with
   the yearly figure), market rate (with change vs the previous month in bps), 3-month ETS
   forecast (with change vs the latest rate). Visuals: rate outlook since 2021 with the 80% band,
   rate stress test (scenario slicer and bars), renewals by month table.
2. **Rates & accuracy.** Headline comparing the best model with ETS, for example "The bond-yield
   model misses by 11.6 bps on average, 39% less than ETS". KPIs and visuals as today (three
   average misses with their relative improvement, pass-through), horizon slicer, history chart,
   rate vs bond yield, ETS vs naive by period.
3. **Payment impact.** Headline for the chosen borrower, for example "A $400k renewal at 4.66%
   adds $495.86 a month". Slicers for balance, years left, rate before, renewal rate and
   threshold; KPIs before/after/change/threshold rate; payment curve; ±50 bps bars.
4. **Renewal triage.** Headline on what triage gains at the chosen capacity, for example
   "Triage reaches 242 more at-risk borrowers than calling by date". Capacity slicer, the four
   KPIs, capacity chart, segments table, "how the call list works" text.
5. **About this project.** No data visuals except the data-freshness label. Sections: the
   problem (renewal shock for 2021 fixed-rate borrowers), who it's for, data (Bank of Canada
   series V122667786 and BD.CDN.5YR.DQ.YLD; simulated 5,000-loan book), methods (ETS damped trend
   chosen by a rule set before the backtest, rolling-origin backtest, 80% band, Diebold-Mariano
   test, bond-yield regression challenger, triage rule), how to read the colours, tools (Python,
   PostgreSQL, Power BI/DAX), author and the GitHub URL.

Headlines and context lines are DAX measures so they follow the slicers and future data.

## Left to the builder

- Exact coordinates, sizes, font sizes and the rail width, provided all goals and anti-goals
  hold (the payment slicer strip must still fit without overflow arrows).
- Exact headline wording and thresholds for ▲/▼, as long as each headline is a true, plain
  sentence computed from data.
- Font family (Segoe UI family or Arial), as long as it's installed with Windows and consistent.
- Whether the rate-outlook legend stays a custom text legend or uses the native legend.
- Wording of the About page, in Canadian English and Neo's first-person voice used in the README.
- How the theme JSON is restructured for the light palette.

## Done when

- `build_pbip.py` rebuilds the project; all tests pass.
- Each of the five pages, opened in Power BI Desktop after a refresh, shows no clipped text,
  scrollbars, overflow arrows or errors, and the reference values match.
- New screenshots of all five pages are in `powerbi/screenshots/`, both READMEs describe the new
  design and the About page, and the change is committed and pushed together with
  `docs/superpowers/` (this document and the plan).
- Files Power BI Desktop writes for this machine only (`.platform`, `cultures/`,
  `diagramLayout.json`, `.pbi/`) stay out of the commit.
