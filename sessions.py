import asyncio
from playwright.async_api import async_playwright
import shutil
from dotenv import load_dotenv
import os

load_dotenv()


async def keep_active(context):
    """Keep Ferfar tabs active by triggering site reset"""
    while True:
        await asyncio.sleep(25)
        for page in context.pages:
            try:
                if "eFerfar" in page.url:
                    await page.evaluate(
                        "if(typeof ResetThisSession === 'function'){ ResetThisSession(); }"
                    )
                    await page.mouse.move(10, 10)
            except:
                pass

async def launch_and_setup():
    # If Ferfar keeps failing, uncomment once and retry
    # shutil.rmtree("./user_data", ignore_errors=True)

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir="./user_data",
            headless=False,
            args=[
                "--remote-debugging-port=9222",
                "--disable-blink-features=AutomationControlled"
            ],
            viewport={"width": 1280, "height": 720}
        )

        page = context.pages[0]

        print("🌐 Opening Mahabhumi...")
        await page.goto(
            "https://digitalsatbara.mahabhumi.gov.in/DSLR",
            wait_until="domcontentloaded",
            timeout=60000
        )

        print("""
==================================================
CHECKPOINT
1. Login manually (captcha + OTP)
2. Go to: Satbara / Mutation
3. Open e-Ferfar manually ONCE
4. Wait till Ferfar page fully loads
==================================================
""")

        input("✅ After e-Ferfar page is open, press ENTER...")

        print("🔍 Verifying Ferfar page...")

        try:
            await page.wait_for_selector(
                "text=Ferfar",
                timeout=15000
            )
            print("✅ Ferfar page detected.")
        except:
            print("❌ Ferfar page not detected.")
            print("➡️ Please CLICK e-Ferfar and wait till page fully loads.")
            return


        url_ferfar = "https://digitalsatbara.mahabhumi.gov.in/DSLR/Satbara/eFerfar"

        async def open_tab(i):
            await asyncio.sleep(i * 2)  # CRITICAL: prevents server crash
            print(f"🚀 Opening Ferfar Tab {i+1}...")
            new_tab = await context.new_page()
            try:
                await new_tab.goto(
                    url_ferfar,
                    wait_until="domcontentloaded",
                    timeout=60000
                )
            except:
                print(f"❌ Ferfar Tab {i+1} failed")

        # Open 9 additional Ferfar tabs (total = TAB_COUNT)
        TAB_COUNT = int(os.getenv("FERFAR_TABS", "5"))
        await asyncio.gather(*[open_tab(i) for i in range(1, TAB_COUNT)])

        print("\n🔥 10 Ferfar tabs opened successfully.")
        await keep_active(context)

if __name__ == "__main__":
    try:
        asyncio.run(launch_and_setup())
    except KeyboardInterrupt:
        print("\n🛑 Session stopped")