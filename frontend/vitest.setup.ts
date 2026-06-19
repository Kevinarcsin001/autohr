import "@testing-library/jest-dom/vitest";

/**
 * vitest 全局 setup。
 *
 * - jest-dom 提供 toBeVisible / toHaveTextContent 等 DOM matcher
 * - localStorage mock：jsdom 默认实现 OK，无需额外处理
 * - URL/location：jsdom 支持
 */

// IntersectionObserver mock（lucide-react 等组件可能用到）
class MockIntersectionObserver {
  observe = () => {};
  unobserve = () => {};
  disconnect = () => {};
  takeRecords = () => [];
}
globalThis.IntersectionObserver =
  MockIntersectionObserver as unknown as typeof IntersectionObserver;

// matchMedia mock（部分组件可能用到）
if (!globalThis.matchMedia) {
  globalThis.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof globalThis.matchMedia;
}
