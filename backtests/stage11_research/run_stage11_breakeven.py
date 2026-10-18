from research_core import OUTPUT_DIR, stage9_break_even


def main():
    results = stage9_break_even()
    results.to_csv(OUTPUT_DIR / "breakeven.csv", index=False)
    table = [
        "# Stage 11 Break-Even Analysis",
        "",
        "Descriptive arithmetic from the existing Stage 9 accepted trades. This does not retest or change that strategy.",
        "",
        "| Metric | Value | Unit |",
        "| --- | ---: | --- |",
    ]
    for row in results.itertuples(index=False):
        value = f"{row.value:,.4f}" if isinstance(row.value, (float, int)) else str(row.value)
        table.append(f"| {row.metric} | {value} | {row.unit} |")
    table.extend(
        [
            "",
            "The break-even win rate uses the observed average gross win and absolute gross loss, plus average cost per trade. It assumes those conditional averages remain fixed and is not a forecast.",
            "",
            "The minimum gross P&L per trade to cover costs is the average cost per trade. It is a currency-P&L threshold, not a price-return threshold; position sizes differ across trades.",
            "",
        ]
    )
    (OUTPUT_DIR / "BREAKEVEN_SUMMARY.md").write_text("\n".join(table), encoding="utf-8")
    print(results.to_string(index=False))
    print(f"\nWrote {OUTPUT_DIR / 'breakeven.csv'}")


if __name__ == "__main__":
    main()