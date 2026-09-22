import "@testing-library/jest-dom";

// jsdom lacks a few browser APIs used by the app shell.
if (!window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({ matches: false, media: query, addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {} }) as any;
}
if (!navigator.clipboard) {
  Object.defineProperty(navigator, "clipboard", { value: { writeText: async () => {} } });
}
if (!window.ResizeObserver) {
  window.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} } as any;
}
