#!/usr/bin/env python3

"""Generate random travel-planning traffic against the ACA travel planner agent."""

from __future__ import annotations

import argparse
import random
import time

import httpx

PROMPTS = [
    "Plan a romantic 5-day honeymoon from Seattle to Paris in June.",
    "I need a week-long family vacation from New York to Tokyo for 4 people in August.",
    "Organise a 3-day weekend getaway from San Francisco to Rome for two foodies.",
    "Plan an adventure trip from London to Tokyo, focused on culture and street food.",
    "Create a 4-day business-and-leisure itinerary from Seattle to Rome in October.",
    "Design a budget-friendly 6-day trip from New York to Paris for a solo traveller.",
    "Surprise anniversary trip from San Francisco to Paris – 5 nights, luxury hotels.",
    "Plan a 7-day art and history tour from London to Rome for two retired teachers.",
    "Family spring-break trip from Seattle to Tokyo – kid-friendly activities please!",
    "Quick 3-day city break from New York to Rome, December. Best Christmas markets.",
]


def run(base_url: str, count: int, delay: float, timeout: float) -> None:
    base_url = base_url.rstrip("/")
    url = f"{base_url}/invoke"
    prompts = [random.choice(PROMPTS) for _ in range(count)]

    print(f"Sending {count} request(s) to {url}  (delay={delay}s)\n")

    for i, prompt in enumerate(prompts, 1):
        print(f"[{i}/{count}] {prompt[:70]}...")
        try:
            resp = httpx.post(
                url,
                json={"prompt": prompt},
                timeout=timeout,
            )
            if resp.status_code == 200:
                result = resp.json().get("result", "")
                preview = result[:120].replace("\n", " ")
                print(f"  -> 200 OK  ({len(result)} chars): {preview}...\n")
            else:
                print(f"  -> {resp.status_code}: {resp.text[:200]}\n")
        except httpx.TimeoutException:
            print("  -> TIMEOUT\n")
        except httpx.ConnectError as exc:
            print(f"  -> CONNECTION ERROR: {exc}\n")

        if i < count:
            time.sleep(delay)

    print("Done.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate random travel-planning traffic for the ACA agent.",
    )
    parser.add_argument(
        "--url",
        default="http://localhost:8080",
        help="Base URL of the travel planner agent (default: http://localhost:8080).",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=1,
        help="Number of requests to send (default: 1).",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=2.0,
        help="Seconds to wait between requests (default: 2).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="HTTP request timeout in seconds (default: 120).",
    )
    args = parser.parse_args()
    run(args.url, args.count, args.delay, args.timeout)


if __name__ == "__main__":
    main()
