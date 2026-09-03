import argparse


def cli() -> None:
    parser = argparse.ArgumentParser(prog="smms")
    parser.add_argument("command", choices=["health", "init-db", "ingest", "scan", "backtest", "paper"])
    args = parser.parse_args()
    if args.command == "health":
        print("Use the API endpoint GET /health for dependency health.")
    else:
        print(f"{args.command} is reserved for a later phase.")


if __name__ == "__main__":
    cli()

