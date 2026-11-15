# Power BI report

A four-page Power BI report built on the CSVs the pipeline exports to `data/powerbi/`. It is saved as a Power BI Project (PBIP): plain-text files that Power BI Desktop opens like a `.pbix`, and that Git can diff.

| Page | Question it answers | What you can change |
|---|---|---|
| Overview | How many renewing borrowers face a big jump, and where is the rate going? | Rate scenario (-50 bps, base, +50 bps) |
| Rates & accuracy | How far off were past forecasts, and does the bond yield help? | Horizon (3 or 6 months) |
| Payment impact | What does renewal cost one borrower? | Balance, years left, old rate, renewal rate, threshold |
| Renewal triage | Who should the team call first with limited capacity? | Calls per week (100 to 400) |

## Screenshots

Screenshots of the four pages will go here once the report has been opened in Power BI Desktop on Windows. They will be saved as `powerbi/screenshots/01_overview.png` to `04_triage.png`.

## Open it (Windows)

1. Install a recent Power BI Desktop. On a version from before 2025, turn on the PBIP and PBIR options under File > Options and settings > Options > Preview features, then restart.
2. Clone the repository: `git clone https://github.com/neonguyen888/Canada-mortgage-renewal-forecast.git`
3. Double-click `powerbi/MortgageRenewal.pbip`, then Home > Refresh. The model reads the CSVs straight from this repository on GitHub. If Power BI asks how to connect to `raw.githubusercontent.com`, choose Anonymous.
4. To read your own local copy instead, go to Home > Transform data > Edit parameters and set `DataFolder` to the full path of `data\powerbi`, for example `C:\Users\you\Canada-mortgage-renewal-forecast\data\powerbi`, then refresh.

Save with File > Save. Power BI Desktop rewrites some files in its own format when it saves, which is expected.

## Check the numbers

With the default slicer choices, these should match the written report:

| Page | Card | Value |
|---|---|---|
| Overview | Loans above +$250 a month | 4,614 (92.3% of 5,000) |
| Overview | Total payment increase | $2.21M |
| Overview | Market benchmark / 3-month forecast | 4.58% / 4.77% |
| Rates & accuracy | ETS / bond-yield model / naive, average miss | 18.9 / 11.7 / 24.4 bps |
| Payment impact | Payment before / after / change | $2,059.72 / $2,538.57 / +$478.85 |
| Payment impact | Rate where the rise hits $250 | 3.48% |
| Renewal triage | Missed with triage / in date order (300 a week) | 1,014 / 1,264 |

`data/powerbi/threshold_reference.csv` lists more threshold rates the Payment impact page should reproduce.

## How it is built

`build_pbip.py` writes every file in this folder: the data model, all 109 visuals, the theme and the four background images. To change the layout, colours or a measure, edit the script and run:

```bash
.venv/bin/python powerbi/build_pbip.py
```

The pipeline runs it as its last step. Rebuilding overwrites changes made in Power BI Desktop, so pick one place to edit.

**Data model.** Each CSV becomes one table through Power Query. The queries read the files with an `en-US` culture, so `4.58` stays 4.58 on a machine set to a comma-decimal locale. `Calendar` links the rate tables by month. The slicer tables (scenario, threshold, balance and so on) are small lookup tables with no relationships; measures read the choice with `SELECTEDVALUE`. That keeps every bar visible when a slicer moves, and the chosen bar turns amber through a colour measure.

**Measures.** All 74 measures live in the `measures_calc` table (Power BI reserves the name `Measures`), grouped in folders: Rates, Renewal book, Accuracy, Payment impact and Triage. The payment formula in DAX is the same Canadian semi-annual one used in `src/mortgage.py` and the SQL function, and the threshold rate is the lowest rate on the 1 bp grid where the rise reaches the threshold.

**Look.** Navy canvas, amber for whatever needs attention, teal for the ETS forecast, light blue for observed data and lavender for the bond-yield model. The panels are drawn into a background image at twice the page size, and the visuals sit on top with transparent backgrounds. Text uses Segoe UI, with DIN for the large numbers. Both fonts ship with Power BI.

## If something goes wrong

- A visual shows an error after refresh: open Transform data and check that `DataFolder` points at the GitHub address or at a folder holding `vw_rate_outlook_band.csv` and the other exports.
- The project won't open: Power BI Desktop names the file and line it couldn't read. The files were checked against Microsoft's published schemas for PBIR, so the message usually points at a version setting in an older Desktop.
- Slicers show as a vertical list instead of buttons: select the slicer and set Slicer settings > Style to Tile.
