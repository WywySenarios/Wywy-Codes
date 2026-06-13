/**
 * Vitest setup for jsdom environment.
 *
 * next-themes calls window.matchMedia("(prefers-color-scheme: dark)")
 * to detect system preference.  jsdom does not implement matchMedia,
 * so we provide a stub that returns a no-op MediaQueryList.
 */

Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: (query: string): MediaQueryList => {
    return {
      matches: query === "(prefers-color-scheme: dark)",
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    } as MediaQueryList;
  },
});
