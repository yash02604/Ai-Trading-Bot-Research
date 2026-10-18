from research_core import FAMILY_VARIANTS, OUTPUT_DIR, run_experiment_set


def main():
    results = run_experiment_set("strategy", FAMILY_VARIANTS)
    results.to_csv(OUTPUT_DIR / "strategy_comparison.csv", index=False)
    print(f"Timeframe/family variants: {len(FAMILY_VARIANTS)}")
    print(results.to_string(index=False))


if __name__ == "__main__":
    main()