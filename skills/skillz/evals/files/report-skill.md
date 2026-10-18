---
name: report
description: Build a weekly sales report from a CSV export.
---
# Report

1. Read `sales.csv`.
2. Run `scripts/summarize.py`. It imports pandas, groups the rows by region with `DataFrame.groupby`, and prints the totals.
3. Write the totals into `report.md` and show them to the user.
