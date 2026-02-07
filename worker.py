import os, asyncio, pika, json
import utils
from playwright.async_api import async_playwright
from dotenv import load_dotenv

log = utils.log
load_dotenv()

# -------------------------------------------------
# EVENT LOOP
# -------------------------------------------------
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)


# -------------------------------------------------
# BROWSER STATE
# -------------------------------------------------
playwright = None
browser = None
context = None
anchor_page = None

# -------------------------------------------------
# INIT BROWSER (WITH LOGIN – ONLY CHANGE)
# -------------------------------------------------
async def init_browser():
    global playwright, browser, context, anchor_page

    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch(
        headless=False,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--start-maximized"
        ]
    )

    context = await browser.new_context(accept_downloads=True)
    anchor_page = await context.new_page()

    log.info("Opening Ferfar login page...")
    await anchor_page.goto(utils.FERFAR_URL, wait_until="domcontentloaded")

    # ---------------- LOGIN LOGIC (STRICT, NO OTHER CHANGES) ----------------
    logged_in = False
    attempt = 0

    while not logged_in and attempt < 5:
        attempt += 1
        log.info(f"Login attempt {attempt}")

        try:
            await anchor_page.wait_for_selector("#txtlogid", timeout=10000)

            await anchor_page.fill("#txtlogid", utils.FERFAR_USER_ID)
            await anchor_page.fill("#txtpasslogin", utils.FERFAR_PASSWORD)

            captcha_img = await anchor_page.locator("#myimg").screenshot()
            captcha_text = await utils.solve_captcha(captcha_img)

            log.info(f"Solved captcha: {captcha_text}")

            if len(captcha_text) == 5:
                await anchor_page.fill("#CaptchaText", captcha_text)
                await asyncio.sleep(5)
                await anchor_page.click("#btnSubmit2")
                await asyncio.sleep(5)

                if "Login" not in anchor_page.url:
                    log.info("LOGIN SUCCESS")
                    logged_in = True
                    break
                else:
                    log.warning("Login failed, retrying...")
            else:
                log.warning("Invalid captcha, refreshing...")
                await anchor_page.click("#myimg")
                await asyncio.sleep(2)

        except Exception as e:
            log.error(f"Login error: {e}")
            await anchor_page.reload()

    if not logged_in:
        raise RuntimeError("Ferfar login failed after 5 attempts")

    log.info("Browser ready (Logged-in Anchor session active)")
    # ----------------------------------------------------------------------

# -------------------------------------------------
# KEEP SESSION ALIVE
# -------------------------------------------------
async def keep_session_alive():
    while True:
        try:
            if anchor_page:
                await anchor_page.evaluate(
                    "() => document.dispatchEvent(new Event('mousemove'))"
                )
        except:
            pass
        await asyncio.sleep(25)



# -------------------------------------------------
# WORKER LOGIC (UNCHANGED)
# -------------------------------------------------
async def run_job(data):
    worker_page = await context.new_page()
    req_id = data["id"]

    try:
        await worker_page.goto(utils.FERFAR_URL, wait_until="domcontentloaded")

        await worker_page.select_option("#ddlDist1", label=data["district"])
        await asyncio.sleep(2)

        await worker_page.select_option("#ddlTahsil", label=data["taluka"])
        await asyncio.sleep(2)

        try:
            await worker_page.select_option("#ddlVillage", label=data["village"])
        except:
            await worker_page.evaluate(
                """v => {
                    const el = document.querySelector('#ddlVillage');
                    const i = [...el.options].findIndex(o => o.text.includes(v));
                    if (i >= 0) {
                        el.selectedIndex = i;
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                    }
                }""",
                data["village"]
            )

        await asyncio.sleep(3)

        if await worker_page.locator(".swal2-confirm").is_visible():
            await worker_page.locator(".swal2-confirm").click()

        mut = worker_page.locator("#txt_mutationno")
        await mut.fill("")
        await mut.type(str(data["mutation_no"]), delay=100)
        await mut.press("Tab")

        await asyncio.sleep(5)

        if await worker_page.locator(".swal2-confirm").is_visible():
            await worker_page.locator(".swal2-confirm").click()
            await asyncio.sleep(1)

        btn = worker_page.locator("input#submit.btn-primary")
        await btn.wait_for(state="visible", timeout=25000)

        if await btn.is_disabled():
            raise Exception("Download button disabled (balance issue)")

        worker_page.on(
            "dialog",
            lambda dialog: asyncio.create_task(dialog.accept())
        )

        async with worker_page.expect_download(timeout=60000) as download_info:
            await btn.click(force=True)

            try:
                swal_ok = worker_page.locator(".swal2-confirm")
                if await swal_ok.is_visible(timeout=3000):
                    await swal_ok.click()
            except:
                pass

        return await download_info.value

    finally:
        await worker_page.close()

# -------------------------------------------------
# RABBITMQ HANDLER
# -------------------------------------------------
def handle_job(ch, method, properties, body):
    data = json.loads(body)
    req_id = data["id"]

    log.info(f"Processing Job ID: {req_id}")

    try:
        download = loop.run_until_complete(run_job(data))

        file_name = f"Ferfar_{data['mutation_no']}_{req_id}.pdf"
        save_path = os.path.join("downloads", file_name)

        loop.run_until_complete(download.save_as(save_path))
        utils.update_db(req_id, "completed", file_name)

        log.info(f"Downloaded: {file_name}")

    except Exception as e:
        log.error(f"Job {req_id} failed: {e}")
        utils.update_db(req_id, "failed")

    ch.basic_ack(delivery_tag=method.delivery_tag)

# -------------------------------------------------
# MAIN
# -------------------------------------------------
if __name__ == "__main__":
    os.makedirs("downloads", exist_ok=True)

    loop.run_until_complete(init_browser())
    loop.create_task(keep_session_alive())

    connection = pika.BlockingConnection(
        pika.ConnectionParameters(host="localhost")
    )
    channel = connection.channel()

    channel.queue_declare(queue="task_queue", durable=True)
    channel.basic_qos(prefetch_count=1)
    channel.basic_consume(queue="task_queue", on_message_callback=handle_job)

    log.info("Ferfar worker running (Auto Login + Auto Download)")

    try:
        channel.start_consuming()
    except KeyboardInterrupt:
        channel.stop_consuming()
        connection.close()