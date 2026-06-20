/**
 * Tests for the Tabs UI component — interactive responsiveness.
 *
 * The component is the official ShadCN `base-nova` Tabs (Base UI variant).
 * This test verifies that tabs provide visual hover feedback.
 */

import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "../../../../src/components/ui/tabs";

describe("Tabs", () => {
  it("renders tab with hover state class for visual feedback", () => {
    render(
      <Tabs defaultValue="a">
        <TabsList>
          <TabsTrigger value="a">Logs</TabsTrigger>
        </TabsList>
        <TabsContent value="a">Content</TabsContent>
      </Tabs>,
    );

    const tab = screen.getByRole("tab", { name: "Logs" });
    expect(tab.className).toMatch(/hover:/);
  });
});
