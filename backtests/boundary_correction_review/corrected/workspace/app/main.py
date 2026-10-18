"""Safe manual entry point for the live paper-only validation session."""


def main():
    # Lazy import keeps tests and static inspection from creating a market session.
    from app.paper_session import run

    run()


if __name__ == "__main__":
    main()
