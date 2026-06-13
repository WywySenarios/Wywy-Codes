import { test, expect } from "@playwright/test";

test.describe("Dashboard page", () => {
  test("renders the empty dashboard correctly", async ({ page }) => {
    await page.goto("/");
    // Wait for the Astro page and React islands to fully hydrate
    await page.waitForLoadState("networkidle");

    // Visual regression: compare against the baseline screenshot.
    // Baseline is stored at tests/e2e/screenshots/dashboard.spec.ts/dashboard-empty.png
    // Run `apps/astro/scripts/update-screenshots.sh` to regenerate baselines.
    await expect(page).toHaveScreenshot("dashboard-empty.png", {
      fullPage: true,
      maxDiffPixelRatio: 0.02,
    });
  });

  test("renders the new-pipeline form correctly", async ({ page }) => {
    await page.goto("/new/");
    await page.waitForLoadState("networkidle");

    await expect(page).toHaveScreenshot("new-pipeline-form.png", {
      fullPage: true,
      maxDiffPixelRatio: 0.02,
    });
  });

  test("renders the inbox page correctly", async ({ page }) => {
    await page.goto("/inbox/");
    await page.waitForLoadState("networkidle");

    await expect(page).toHaveScreenshot("inbox.png", {
      fullPage: true,
      maxDiffPixelRatio: 0.02,
    });
  });
});
