"use client";

import { ThemeProvider } from "next-themes";
import { ThemeToggle } from "./theme-toggle";

/**
 * Wraps next-themes ThemeProvider around the ThemeToggle so useTheme()
 * resolves.  Rendered as a client:load island in nav.astro.
 *
 * ThemeProvider manages the `class` attribute on <html> — no other
 * wrapping is needed.  An inline <script> in layout.astro prevents
 * the flash of wrong theme on first paint.
 */
export function ThemeSetup() {
  return (
    <ThemeProvider attribute="class" defaultTheme="system" enableSystem>
      <ThemeToggle />
    </ThemeProvider>
  );
}
