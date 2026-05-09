import argparse
import time

import requests


def main():
    parser = argparse.ArgumentParser(description="Poll pending reminders API.")
    parser.add_argument("--member-id", required=True, type=int)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--interval", default=5, type=int)
    args = parser.parse_args()

    endpoint = f"{args.base_url.rstrip('/')}/api/reminders/pending/"

    while True:
        try:
            response = requests.get(
                endpoint, params={"member_id": args.member_id}, timeout=10
            )
            response.raise_for_status()
            reminders = response.json()
            for reminder in reminders:
                print(f"[Reminder #{reminder['id']}] {reminder['message']}")
        except Exception as exc:
            print(f"Polling error: {exc}")

        time.sleep(max(args.interval, 1))


if __name__ == "__main__":
    main()
