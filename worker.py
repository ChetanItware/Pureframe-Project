import sys, asyncio, os, io, pika, json, psycopg2
from playwright.async_api import async_playwright

# Encoding Fix for Windows/CMD
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Database Update Function
def update_db(req_id, status, filename=None):
    try:
        conn = psycopg2.connect(
            dbname="postgres",
            user="postgres",
            password="chetan",
            host="localhost",
            port="5432"
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
        print(f"✅ DB Updated: ID {req_id} is {status}")
    except Exception as e:
        print(f"❌ DB Error: {e}")

async def run_automation(data):
    req_id = data['id']
    dist, tal, vil, mut_no = (
        data['district'],
        data['taluka'],
        data['village'],
        str(data['mutation_no'])
    )

    async with async_playwright() as p:
        try:
            browser = await p.chromium.connect_over_cdp("http://localhost:9222")
            context = browser.contexts[0]
            page = await context.new_page()

            # ✅ ADDING YOUR SCRIPT (AS-IS)
            await page.add_init_script("""
                // Kill native dialogs
                window.alert = () => true;
                window.confirm = () => true;
                window.prompt = () => null;

                

                // Auto-click swal OK if it still renders
                const obs = new MutationObserver(() => {
                    const btn = document.querySelector('.swal2-confirm');
                    if (btn) btn.click();
                });
                obs.observe(document.body, { childList: true, subtree: true });
            """)

            await page.goto(
                "https://digitalsatbara.mahabhumi.gov.in/DSLR/Satbara/eFerfar",
                wait_until="domcontentloaded"
            )

            js_logic = """
            async (args) => {
                const wait = (ms) => new Promise(r => setTimeout(r, ms));

                const select = async (selector, value) => {
                    const el = document.querySelector(selector);
                    if (!el) return false;

                    for (let i = 0; i < 50; i++) {
                        const options = [...el.options].map(o => o.text.trim());
                        const idx = options.findIndex(t => t.includes(value.trim()));
                        if (idx !== -1) {
                            el.selectedIndex = idx;
                            el.dispatchEvent(new Event('change', { bubbles: true }));
                            return true;
                        }
                        await wait(300);
                    }
                    return false;
                };

                if (!await select('#ddlDist1', args.dist)) return "DIST_ERR";
                await wait(3000);

                if (!await select('#ddlTahsil', args.tal)) return "TAL_ERR";
                await wait(4000);

                if (!await select('#ddlVillage', args.vil)) return "VIL_ERR";
                await wait(2000);

                for (let i = 0; i < 20; i++) {
                    const okBtn = document.querySelector('.swal2-confirm');
                    if (okBtn) {
                        okBtn.click();
                        break;
                    }
                    await wait(400);
                }

                await wait(3000);

                const mutInput = document.querySelector('#txt_mutationno');
                if (!mutInput) return "MUT_INPUT_NOT_FOUND";

                mutInput.focus();
                mutInput.value = '';
                await wait(300);

                for (const ch of args.mut_no) {
                    mutInput.value += ch;
                    mutInput.dispatchEvent(new KeyboardEvent('keydown', { key: ch, bubbles: true }));
                    mutInput.dispatchEvent(new KeyboardEvent('keyup', { key: ch, bubbles: true }));
                    mutInput.dispatchEvent(new Event('input', { bubbles: true }));
                    await wait(300);
                }

                mutInput.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true }));
                mutInput.blur();
                await wait(4000);

                let downloadBtn = null;
                for (let i = 0; i < 20; i++) {
                    downloadBtn = document.querySelector('#submit');
                    if (downloadBtn && !downloadBtn.disabled) break;
                    await wait(500);
                }

                if (!downloadBtn) return "DOWNLOAD_BTN_NOT_FOUND";

                downloadBtn.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
                downloadBtn.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
                downloadBtn.click();

                return "OK";
            }
            """

            res = await page.evaluate(
                js_logic,
                {"dist": dist, "tal": tal, "vil": vil, "mut_no": mut_no}
            )

            if res == "OK":
                async with page.expect_download() as download_info:
                    pass  # swal OK auto-clicked by observer

                download = await download_info.value
                file_name = f"Ferfar_{mut_no}_{req_id}.pdf"
                await download.save_as(f"./downloads/{file_name}")

                update_db(req_id, "completed", file_name)
            else:
                print(f"⚠️ Automation Failed: {res}")
                update_db(req_id, "failed")

        except Exception as e:
            print(f"❌ Error: {e}")
            update_db(req_id, "failed")
        finally:
            await page.close()

def callback(ch, method, properties, body):
    try:
        data = json.loads(body)
        print(f"📥 Received Ferfar Task for ID: {data['id']}")
        asyncio.run(run_automation(data))
        ch.basic_ack(delivery_tag=method.delivery_tag)
    except Exception as e:
        print(f"❌ Callback Error: {e}")

if __name__ == "__main__":
    if not os.path.exists("downloads"):
        os.makedirs("downloads")

    try:
        connection = pika.BlockingConnection(
            pika.ConnectionParameters(host="localhost")
        )
        channel = connection.channel()
        channel.queue_declare(queue="task_queue", durable=True)
        channel.basic_qos(prefetch_count=1)
        channel.basic_consume(queue="task_queue", on_message_callback=callback)

        print("🚀 Ferfar Worker is running and waiting for tasks...")
        channel.start_consuming()
    except Exception as e:
        print(f"❌ RabbitMQ/Worker start failed: {e}")
