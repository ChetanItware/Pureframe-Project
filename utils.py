# utils.py
# =================================================
import os
import json
import logging
import psycopg2
import base64
import httpx

from dotenv import load_dotenv

# -------------------------------------------------
# ENV
# -------------------------------------------------
load_dotenv()
FERFAR_USER_ID=os.getenv("FERFAR_USER_ID")  
FERFAR_PASSWORD=os.getenv("FERFAR_PASSWORD")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
FERFAR_URL = os.getenv("FERFAR_URL")

# -------------------------------------------------
# LOGGING
# -------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("ferfar-worker")

# -------------------------------------------------
# CAPTCHA SOLVER
# -------------------------------------------------
async def solve_captcha(image_bytes: bytes) -> str:
    """
    Solves captcha image using OpenAI Vision
    """
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY not found in environment")

    image_b64 = base64.b64encode(image_bytes).decode("utf-8")

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "gpt-4o-mini",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "Read the captcha and return ONLY the text."
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{image_b64}"
                                }
                            }
                        ]
                    }
                ],
                "max_tokens": 10
            }
        )

        result = response.json()
        return result["choices"][0]["message"]["content"].strip()

# -------------------------------------------------
# DATABASE
# -------------------------------------------------
def update_db(req_id: int, status: str, filename: str | None = None) -> None:
    """
    Updates extraction_requests table
    """
    try:
        conn = psycopg2.connect(
            dbname=os.getenv("DB_NAME"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
            host=os.getenv("DB_HOST"),
            port=os.getenv("DB_PORT"),
        )

        cur = conn.cursor()

        if filename:
            cur.execute(
                """
                UPDATE extraction_requests
                SET status = %s, pdf_url = %s
                WHERE id = %s
                """,
                (status, filename, req_id),
            )
        else:
            cur.execute(
                """
                UPDATE extraction_requests
                SET status = %s
                WHERE id = %s
                """,
                (status, req_id),
            )

        conn.commit()
        cur.close()
        conn.close()

        log.info(f"DB updated → Job {req_id} : {status}")

    except Exception as e:
        log.error(f"DB update failed for Job {req_id}: {e}")

