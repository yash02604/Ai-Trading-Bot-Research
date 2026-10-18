from research_core import FILTER_VARIANTS, OUTPUT_DIR, run_experiment_set


def main():
    results = run_experiment_set("filter", FILTER_VARIANTS)
    results.to_csv(OUTPUT_DIR / "filter_experiments.csv", index=False)
    print(f"Filter variants/combinations tried: {len(FILTER_VARIANTS)}")
    print(results.to_string(index=False))


if __name__ == "__main__":
    main()