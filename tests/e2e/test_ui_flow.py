
import asyncio

import pytest

pytest.importorskip("playwright", reason="playwright not installed; skip e2e UI test")

from playwright.async_api import async_playwright, expect

async def run_ui_test():
    print("🧪 Starting E2E UI Test...")
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        context = await browser.new_context()
        page = await context.new_page()
        
        # Capture logs to find the "White Screen" cause
        page.on("console", lambda msg: print(f"BROWSER LOG: {msg.text}"))
        page.on("pageerror", lambda exc: print(f"BROWSER ERROR: {exc}"))

        # Monitor Network Responses
        page.on("response", lambda response: check_response(response))

        def check_response(response):
            if "index-d05455d6.js" in response.url: return # Ignore JS
            if response.request.resource_type in ["fetch", "websocket", "xhr"]:
                # If we get HTML for an API call, that's the bug
                if "text/html" in response.headers.get("content-type", ""):
                    print(f"🚨 SUSPICIOUS RESPONSE: {response.url} returned HTML!")

        try:
            # 1. Load Page
            print("➡️  Loading https://deck.mumega.com ...")
            await page.goto("https://deck.mumega.com")
            
            # Assert Title
            await expect(page).to_have_title("SOS | Empire of the Mind")
            print("✅ Title Verified")

            # 2. Assert Login Screen
            inoculate_btn = page.locator('text=INOCULATE')
            await expect(inoculate_btn).to_be_visible()
            print("✅ Login Screen Loaded")

            # 3. Perform Interaction (The "White Screen" Trigger)
            print("➡️  Clicking INOCULATE...")
            await inoculate_btn.click()
            
            # 4. Assert Transition
            # We expect to see the Dashboard Header
            dashboard_header = page.locator('h4:has-text("EMPIRE OF THE MIND")')
            
            # Wait with a timeout to catch the freeze
            try:
                await dashboard_header.wait_for(state="visible", timeout=5000)
                print("✅ Transition Successful: Dashboard Visible")
            except Exception:
                print("❌ TEST FAILED: Dashboard did not appear after click.")
                print("   Checking current state...")
                content = await page.content()
                print(f"   DOM dump: {content[:500]}")
                await page.screenshot(path="debug_white_screen.png")
                
        except Exception as e:
            print(f"❌ EXECUTION ERROR: {e}")
            
        await browser.close()

if __name__ == "__main__":
    asyncio.run(run_ui_test())
