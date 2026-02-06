import os, json, asyncio, pika, psycopg2, logging
from playwright.async_api import async_playwright
from dotenv import load_dotenv

# -------------------------------------------------
# ENV + LOGGING
# -------------------------------------------------
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("ferfar-worker")

FERFAR_URL = os.getenv("FERFAR_URL")
CDP_URL = os.getenv("CDP_URL")

# -------------------------------------------------
# EVENT LOOP (single, explicit)
# -------------------------------------------------
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

# -------------------------------------------------
# DB
# -------------------------------------------------
def update_db(req_id, status, filename=None):
    conn = psycopg2.connect(
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT")
    )
    cur = conn.cursor()
    if filename:
        cur.execute(
            "UPDATE extraction_requests SET status=%s, pdf_url=%s WHERE id=%s",
            (status, filename, req_id)
        )
    else:
        cur.execute(
            "UPDATE extraction_requests SET status=%s WHERE id=%s",
            (status, req_id)
        )
    conn.commit()
    cur.close()
    conn.close()
    log.info(f"DB updated: {req_id} → {status}")

# -------------------------------------------------
# BROWSER STATE
# -------------------------------------------------
playwright = None
browser = None
context = None
anchor_page = None
worker_page = None

# -------------------------------------------------
# INIT BROWSER
# -------------------------------------------------
async def init_browser():
    global playwright, browser, context, anchor_page, worker_page

    playwright = await async_playwright().start()
    browser = await playwright.chromium.connect_over_cdp(CDP_URL)

    context = browser.contexts[0]

    # Anchor tab (session only)
    anchor_page = await context.new_page()
    await anchor_page.goto(FERFAR_URL, wait_until="domcontentloaded")

    log.info("Browser ready: anchor + worker pages created")

# -------------------------------------------------
# KEEP SESSION ALIVE (ANCHOR ONLY)
# -------------------------------------------------
async def keep_session_alive():
    while True:
        if "Login" in anchor_page.url or "captcha" in anchor_page.url.lower():
            log.error("❌ Anchor session expired. Manual relogin required.")
        try:
            await anchor_page.evaluate("""
                () => {
                    document.dispatchEvent(new Event('mousemove'));
                    document.dispatchEvent(new Event('visibilitychange'));
                }
            """)
            log.debug("Session heartbeat sent")
        except Exception as e:
            log.warning(f"Heartbeat failed: {e}")
        await asyncio.sleep(25)

# -------------------------------------------------
# WORKER LOGIC
# -------------------------------------------------
async def run_job(data):
    global worker_page

    req_id = data["id"]
    log.info(f"Job {req_id}: starting")

    worker_page = await context.new_page()

    try:
        await worker_page.goto(FERFAR_URL, wait_until="domcontentloaded")

        await worker_page.select_option("#ddlDist1", label=data["district"])
        await asyncio.sleep(3)

        await worker_page.select_option("#ddlTahsil", label=data["taluka"])
        await asyncio.sleep(4)

        try:
            await worker_page.select_option("#ddlVillage", label=data["village"])
        except:
            await worker_page.evaluate("""
                v => {
                const el = document.querySelector('#ddlVillage');
                const i = [...el.options].findIndex(o => o.text.includes(v));
                if (i >= 0) {
                    el.selectedIndex = i;
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                }
                }
            """, data["village"])

        await asyncio.sleep(2)

        await worker_page.locator(".swal2-confirm").click(timeout=15000)
        await asyncio.sleep(3)

        mut = worker_page.locator("#txt_mutationno")
        await mut.fill("")
        await mut.type(str(data["mutation_no"]), delay=300)
        await mut.press("Tab")
        await asyncio.sleep(4)

        btn = worker_page.locator("input#submit")

        # 1️⃣ wait till button is visible + not disabled attribute
        await btn.wait_for(state="visible", timeout=20000)
        await worker_page.wait_for_function(
            "el => !el.disabled",
            btn
        )

        # 2️⃣ small human delay (site-side debounce)
        await asyncio.sleep(1)

        # 3️⃣ real user click + download capture
        async with worker_page.expect_download(timeout=30000) as d:
            await btn.click(force=True)

        download = await d.value

        # await btn.wait_for(state="enabled", timeout=15000)

        # async with worker_page.expect_download() as d:
        #     await btn.click()

        # return await d.value

    finally:
        await worker_page.close()
        log.info(f"Job {req_id}: closed worker page")

# -------------------------------------------------
# RABBITMQ HANDLER
# -------------------------------------------------
def handle_job(ch, method, body):
    data = json.loads(body)
    req_id = data["id"]

    try:
        download = loop.run_until_complete(run_job(data))
        file_name = f"Ferfar_{data['mutation_no']}_{req_id}.pdf"
        loop.run_until_complete(download.save_as(f"./downloads/{file_name}"))
        update_db(req_id, "completed", file_name)
    except Exception:
        log.exception(f"Job {req_id} failed")
        update_db(req_id, "failed")

    ch.basic_ack(method.delivery_tag)

# -------------------------------------------------
# MAIN
# -------------------------------------------------
if __name__ == "__main__":
    os.makedirs("downloads", exist_ok=True)

    loop.run_until_complete(init_browser())
    loop.create_task(keep_session_alive())

    connection = pika.BlockingConnection(pika.ConnectionParameters(host="localhost"))
    channel = connection.channel()
    channel.queue_declare(queue="task_queue", durable=True)
    channel.basic_qos(prefetch_count=1)

    channel.basic_consume(
        queue="task_queue",
        on_message_callback=lambda ch, m, p, b: handle_job(ch, m, b)
    )

    log.info("Ferfar worker running (anchor + worker model)")
    channel.start_consuming()
