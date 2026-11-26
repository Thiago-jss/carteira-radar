"""Exercise the production dashboard in Chromium; optionally capture synthetic evidence."""

import os
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def main():
    origin = "http://127.0.0.1:8084"
    with sync_playwright() as browser:
        chromium = browser.chromium.launch()
        try:
            page = chromium.new_page(viewport={"width": 1460, "height": 1060}, locale="pt-BR")
            errors = []
            page.on("pageerror", lambda error: errors.append(type(error).__name__))
            page.route(
                "**/*",
                lambda route: (
                    route.continue_()
                    if route.request.url.startswith(origin + "/")
                    else route.abort()
                ),
            )
            page.goto(origin, wait_until="domcontentloaded", timeout=15000)
            expect(page.get_by_role("heading", name="Painel de risco")).to_be_visible()
            expect(page.get_by_role("table", name="Comparação no teste histórico")).to_be_visible()
            expect(page.get_by_text("Última competência da base:", exact=False)).to_be_visible()
            rows = page.get_by_role("region", name="Entidades monitoradas").get_by_role("row")
            expect(rows.nth(1)).to_be_visible()
            if screenshot := os.environ.get("RADAR_SCREENSHOT"):
                expect(page.get_by_text("Demonstração com dados fictícios")).to_be_visible()
                Path(screenshot).parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=screenshot, animations="disabled")
            rows.nth(1).get_by_role("button").click()
            dialog = page.get_by_role("dialog", name="Histórico da entidade")
            expect(dialog.get_by_role("heading", name="O que pesou no escore")).to_be_visible()
            expect(dialog.get_by_role("region", name="Série histórica")).to_be_visible()
            dialog.get_by_role("button", name="Fechar").click()
            expect(dialog).to_be_hidden()
            picker = page.get_by_label("Competência")
            picker.select_option(index=1)
            expect(page.get_by_text("Teste histórico:", exact=False)).to_be_visible()
            assert not errors, errors
        finally:
            chromium.close()
    print(
        "PASS: cohort selection, explanations and history rendered from the running API in Chromium."
    )


if __name__ == "__main__":
    main()
