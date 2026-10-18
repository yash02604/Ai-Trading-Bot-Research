from research_core import EXIT_VARIANTS, OUTPUT_DIR, run_experiment_set


def main():
    results = run_experiment_set("exit", EXIT_VARIANTS)
    results.to_csv(OUTPUT_DIR / "exit_experiments.csv", index=False)
    print(f"Exit variants: {len(EXIT_VARIANTS)}")
    print(results.to_string(index=False))


if __name__ == "__main__":
    main()