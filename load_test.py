import math
import os
import statistics
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

API_KEY = os.getenv("API_KEY", "").strip()
BASE_URL = "http://127.0.0.1:8001"

PROVIDERS = {
    "JioFiber": "/api/jio/plans",
    "ACT Fiber": "/api/act/plans",
    "Hathway": "/api/hathway/plans",
}

REQUESTS_PER_TEST = 50
CONCURRENCY_LEVELS = (1, 5, 10)

if not API_KEY:
    raise SystemExit("API_KEY is missing from .env")


def request_once(endpoint):
    request = Request(
        BASE_URL + endpoint,
        headers={
            "X-API-Key": API_KEY,
            "Accept": "application/json",
        },
    )

    started = time.perf_counter()

    try:
        with urlopen(request, timeout=15) as response:
            body = response.read()
            status = str(response.status)

        return {
            "status": status,
            "ms": (time.perf_counter() - started) * 1000,
            "bytes": len(body),
            "body": body,
        }

    except HTTPError as error:
        error.close()
        status = str(error.code)

    except Exception as error:
        status = type(error).__name__

    return {
        "status": status,
        "ms": (time.perf_counter() - started) * 1000,
        "bytes": 0,
        "body": b"",
    }


def percentile(values, percentage):
    ordered = sorted(values)
    index = math.ceil(len(ordered) * percentage / 100) - 1
    return ordered[max(0, index)]


def test_provider(name, endpoint):
    import json

    print(f"\n{'=' * 65}")
    print(f"{name}: {endpoint}")

    # Check the endpoint and show whether it contains actual plans.
    first = request_once(endpoint)

    if first["status"] != "200":
        print(f"Skipped: initial request returned {first['status']}")
        return

    try:
        payload = json.loads(first["body"])
        count = payload.get("count", "not provided")
        print(f"Saved plan count: {count}")

        if count == 0:
            print("NOTE: this endpoint is returning an empty dataset.")
    except (ValueError, AttributeError):
        print("NOTE: response does not have the expected JSON structure.")

    print(f"Initial request: {first['ms']:.2f} ms")

    # Warm-up requests are excluded from the results below.
    for _ in range(3):
        request_once(endpoint)

    for concurrency in CONCURRENCY_LEVELS:
        started = time.perf_counter()

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            results = list(
                executor.map(
                    request_once,
                    [endpoint] * REQUESTS_PER_TEST,
                )
            )

        duration = time.perf_counter() - started
        successful = [
            result for result in results
            if result["status"] == "200"
        ]
        errors = Counter(
            result["status"]
            for result in results
            if result["status"] != "200"
        )

        print(
            f"\nConcurrent requests: {concurrency} | "
            f"Total: {REQUESTS_PER_TEST}"
        )
        print(
            f"Successful: {len(successful)} | "
            f"Failed: {REQUESTS_PER_TEST - len(successful)}"
        )

        if successful:
            timings = [result["ms"] for result in successful]

            print(
                f"Average: {statistics.mean(timings):.2f} ms | "
                f"Median: {statistics.median(timings):.2f} ms"
            )
            print(
                f"P95: {percentile(timings, 95):.2f} ms | "
                f"Slowest: {max(timings):.2f} ms"
            )
            print(
                "Successful throughput: "
                f"{len(successful) / duration:.2f} requests/sec"
            )

        if errors:
            print(f"Errors: {dict(errors)}")


if __name__ == "__main__":
    for name, endpoint in PROVIDERS.items():
        test_provider(name, endpoint)