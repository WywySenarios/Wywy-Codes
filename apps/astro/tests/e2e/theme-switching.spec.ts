import { test, expect } from "@playwright/test";

test.describe("Theme switching", () => {
  test("toggles between light and dark mode", async ({ page }) => {
    await page.goto("/");
    await page.waitForLoadState("networkidle");

    // The theme toggle button must be present
    const toggle = page.getByRole("button", { name: /toggle theme/i });
    await expect(toggle).toBeVisible();

    // Initial state: verify a class is present on <html> (dark or light)
    const html = page.locator("html");
    const initialClass = await html.getAttribute("class");
    expect(initialClass).toMatch(/dark|light/);

    // Click to switch theme
    await toggle.click();
    await page.waitForTimeout(300); // allow class change to propagate

    const afterFirstClick = await html.getAttribute("class");
    expect(afterFirstClick).not.toBe(initialClass);

    // Click again to switch back
    await toggle.click();
    await page.waitForTimeout(300);

    const afterSecondClick = await html.getAttribute("class");
    expect(afterSecondClick).toBe(initialClass);
  });

  test("dashboard renders correctly in light mode", async ({ page }) => {
    await page.goto("/");
    await page.waitForLoadState("networkidle");

    // Ensure we're in light mode
    const html = page.locator("html");
    const currentClass = await html.getAttribute("class") || "";
    if (currentClass.includes("dark")) {
      const toggle = page.getByRole("button", { name: /toggle theme/i });
      await toggle.click();
      await page.waitForTimeout(300);
    }

    await expect(page).toHaveScreenshot("dashboard-light.png", {
      fullPage: true,
      maxDiffPixelRatio: 0.02,
    });
  });

  test("dashboard renders correctly in dark mode", async ({ page }) => {
    await page.goto("/");
    await page.waitForLoadState("networkidle");

    // Ensure we're in dark mode
    const html = page.locator("html");
    const currentClass = await html.getAttribute("class") || "";
    if (!currentClass.includes("dark")) {
      const toggle = page.getByRole("button", { name: /toggle theme/i });
      await toggle.click();
      await page.waitForTimeout(300);
    }

    await expect(page).toHaveScreenshot("dashboard-dark.png", {
      fullPage: true,
      maxDiffPixelRatio: 0.02,
    });
  });
});
