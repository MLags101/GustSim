import '@testing-library/jest-dom/vitest';

// jsdom lacks these browser APIs used by the UI.
if (!window.matchMedia) {
  window.matchMedia = (query: string) => ({ matches: false, media: query, onchange: null, addListener: () => {}, removeListener: () => {},
    addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => false }) as unknown as MediaQueryList;
}
if (!('ResizeObserver' in window)) {
  (window as any).ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
}
