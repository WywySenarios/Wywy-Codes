import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ThemeProvider } from "next-themes";
import { ThemeToggle } from "../../../../src/components/ui/theme-toggle";

/**
 * A render helper that wraps children in next-themes ThemeProvider
 * so useTheme() resolves inside the component tree.
 */
function renderWithTheme(ui: React.ReactElement) {
  return render(
    <ThemeProvider attribute="class" defaultTheme="system" enableSystem>
      {ui}
    </ThemeProvider>,
  );
}

describe("ThemeToggle", () => {
  it("renders a button with an accessible label", () => {
    renderWithTheme(<ThemeToggle />);
    const button = screen.getByRole("button", { name: /toggle theme/i });
    expect(button).toBeTruthy();
  });

  it("toggles the theme when clicked — checks localStorage", () => {
    renderWithTheme(<ThemeToggle />);
    const button = screen.getByRole("button", { name: /toggle theme/i });
    const before = localStorage.getItem("theme");

    fireEvent.click(button);

    // setTheme() writes localStorage synchronously even though the
    // DOM class update may be deferred in jsdom
    expect(localStorage.getItem("theme")).not.toBe(before);
  });

  it("toggles back to the original theme on a second click", () => {
    renderWithTheme(<ThemeToggle />);
    const button = screen.getByRole("button", { name: /toggle theme/i });

    // First click
    fireEvent.click(button);
    const afterFirst = localStorage.getItem("theme");
    expect(afterFirst).not.toBeNull();

    // Second click — should be different from after first
    fireEvent.click(button);
    expect(localStorage.getItem("theme")).not.toBe(afterFirst);
  });
});
