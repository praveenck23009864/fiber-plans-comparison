import json
import os
import secrets
import sqlite3
from contextlib import closing
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Security
from fastapi.security import APIKeyHeader


# Locate files relative to main.py.
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "jio_fiber.db"

# Read your existing API key.
load_dotenv(BASE_DIR / ".env")
API_KEY = os.getenv("API_KEY", "").strip()

if not API_KEY:
    raise RuntimeError(
        "API_KEY is missing. Add API_KEY=your_secret_key "
        "to the .env file beside main.py."
    )


app = FastAPI(
    title="JioFiber Prepaid Plans API",
    description="Read scraped JioFiber plans using your API key.",
    version="1.0.0",
)

key_header = APIKeyHeader(
    name="X-API-Key",
    auto_error=False,
)


def verify_key(key: str | None = Security(key_header)):
    """Reject requests with a missing or incorrect API key."""
    if not key or not secrets.compare_digest(
        key.encode("utf-8"),
        API_KEY.encode("utf-8"),
    ):
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid API key",
        )


@app.get(
    "/api/jio/plans",
    tags=["JioFiber"],
    dependencies=[Security(verify_key)],
)
@app.get(
    "/api/act/plans",
    tags=["ACT Fibernet"],
    dependencies=[Security(verify_key)],
)
def get_act_plans(city: str = "Chennai"):
    """Return ACT plans from the ACT table."""
    if not DB_PATH.is_file():
        raise HTTPException(
            status_code=503,
            detail="Database missing. Run act_scraper.py first.",
        )

    city = city.strip()

    if not city:
        raise HTTPException(
            status_code=422,
            detail="City must not be empty.",
        )

    try:
        with closing(
            sqlite3.connect(
                DB_PATH.as_uri() + "?mode=ro",
                uri=True,
                timeout=10,
            )
        ) as connection:
            rows = connection.execute(
                """
                SELECT details_json
                FROM act_plans
                WHERE city = ? COLLATE NOCASE
                ORDER BY price_inr, plan_id
                """,
                (city,),
            ).fetchall()

        plans = [json.loads(row[0]) for row in rows]

        return {
            "provider": "ACT Fibernet",
            "city": city,
            "count": len(plans),
            "data": plans,
        }

    except sqlite3.Error as error:
        raise HTTPException(
            status_code=503,
            detail="ACT data is unavailable.",
        ) from error

    except json.JSONDecodeError as error:
        raise HTTPException(
            status_code=500,
            detail="A saved ACT plan contains invalid JSON.",
        ) from error
def get_plans(category: str | None = None):
    """Return saved plans, optionally filtered by category."""
    if not DB_PATH.is_file():
        raise HTTPException(
            status_code=503,
            detail="Database missing. Run scraper.py first.",
        )

    category = category.strip() if category else None

    try:
        # Open SQLite in read-only mode.
        with closing(
            sqlite3.connect(
                DB_PATH.as_uri() + "?mode=ro",
                uri=True,
                timeout=10,
            )
        ) as connection:
            connection.row_factory = sqlite3.Row

            query = "SELECT details_json FROM plan_details"
            parameters = []

            if category:
                query += " WHERE category = ? COLLATE NOCASE"
                parameters.append(category)

            query += " ORDER BY category, price_inr"

            rows = connection.execute(
                query, parameters
            ).fetchall()

        plans = [
            json.loads(row["details_json"])
            for row in rows
        ]

        return {
            "count": len(plans),
            "data": plans,
        }

    except sqlite3.Error as error:
        raise HTTPException(
            status_code=503,
            detail=(
                "Detailed plans unavailable. "
                "Run scraper.py successfully first."
            ),
        ) from error

    except json.JSONDecodeError as error:
        raise HTTPException(
            status_code=500,
            detail="A saved plan contains invalid JSON.",
        ) from error
@app.get(
    "/api/hathway/plans",
    tags=["Hathway"],
    dependencies=[Security(verify_key)],
)
def get_hathway_plans(city: str = "Chennai"):
    if not DB_PATH.is_file():
        raise HTTPException(
            status_code=503,
            detail="Database not found. Run the scraper first.",
        )

    try:
        with closing(
            sqlite3.connect(
                DB_PATH.as_uri() + "?mode=ro",
                uri=True,
            )
        ) as connection:
            rows = connection.execute(
                """
                SELECT details_json
                FROM hathway_plans
                WHERE city = ? COLLATE NOCASE
                ORDER BY advertised_price_inr
                """,
                (city,),
            ).fetchall()

        plans = [json.loads(row[0]) for row in rows]

        return {
            "provider": "Hathway",
            "city": city,
            "count": len(plans),
            "data": plans,
        }

    except sqlite3.Error:
        raise HTTPException(
            status_code=503,
            detail=(
                "Hathway data is unavailable. "
                "Run hathway_scraper.py successfully first."
            ),
        )

    except json.JSONDecodeError:
        raise HTTPException(
            status_code=500,
            detail="A saved Hathway plan contains invalid JSON.",
        )
