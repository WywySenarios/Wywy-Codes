import { describe, it, expect } from "vitest";
import { cn } from "../../../src/lib/utils";

/**
 * cn() is the shadcn/ui class-name merge utility.
 * It combines clsx + tailwind-merge so conflicting Tailwind classes resolve correctly.
 * Required by convention: react.mdx and tailwind.mdx specify shadcn/ui components
 * are styled via cn() from src/lib/utils.ts.
 */
describe("cn utility", () => {
  it("merges multiple class name strings", () => {
    expect(cn("px-4", "py-2")).toBe("px-4 py-2");
  });

  it("resolves conflicting Tailwind classes — last wins", () => {
    expect(cn("px-4", "px-6")).toBe("px-6");
  });

  it("removes falsy values (undefined, null, false)", () => {
    expect(cn("base", false && "hidden", undefined, null, "extra")).toBe("base extra");
  });

  it("returns an empty string when given no truthy classes", () => {
    expect(cn(false && "hidden", undefined, null)).toBe("");
  });

  it("handles conditional class objects (clsx syntax)", () => {
    expect(cn("base", { active: true, disabled: false })).toBe("base active");
  });

  it("merges Tailwind color classes — last utility wins", () => {
    expect(cn("text-red-500", "text-blue-500")).toBe("text-blue-500");
  });
});
