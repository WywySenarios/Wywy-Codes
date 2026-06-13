import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { Button } from "../../../../src/components/ui/button";

/**
 * shadcn/ui Button component tests — base-nova style.
 *
 * Conventions (react.mdx, tailwind.mdx): shadcn/ui primitives live in
 * src/components/ui/ with kebab-case filenames, styled via cn() from
 * src/lib/utils.ts.
 */
describe("Button", () => {
  it("renders its children as the button label", () => {
    render(<Button>Save</Button>);
    expect(screen.getByRole("button", { name: "Save" })).toBeTruthy();
  });

  it("applies destructive styling when variant is destructive", () => {
    render(<Button variant="destructive">Delete</Button>);
    const button = screen.getByRole("button", { name: "Delete" });
    expect(button.className).toMatch(/destructive/);
  });

  it("supports a size prop that changes dimensions", () => {
    render(<Button size="lg">Large</Button>);
    const button = screen.getByRole("button", { name: "Large" });
    // base-nova lg size: h-9 (vs default h-8)
    expect(button.className).toContain("h-9");
  });

  it("supports the outline variant", () => {
    render(<Button variant="outline">Outline</Button>);
    const button = screen.getByRole("button", { name: "Outline" });
    // outline variant has a visible border
    expect(button.className).toContain("border");
  });

  it("applies both variant and size classes together", () => {
    render(
      <Button variant="secondary" size="sm">
        Small Secondary
      </Button>,
    );
    const button = screen.getByRole("button", { name: "Small Secondary" });
    expect(button.className).toContain("secondary");
    // base-nova sm size: h-7 (vs default h-8)
    expect(button.className).toContain("h-7");
  });
});
