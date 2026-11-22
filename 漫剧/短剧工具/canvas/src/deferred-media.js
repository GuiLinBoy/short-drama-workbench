const roots = new WeakMap();

export const mediaVersion = resource => resource?.exists
  ? JSON.stringify([resource.url, resource.size, resource.modified, resource.sha256]) : null;

// One observer per canvas. Coordinates are calculated by the browser after the
// ReactFlow pan/zoom transform and clipped to the canvas, not the whole page.
export function observeCanvasMedia(element, ready, Observer = globalThis.IntersectionObserver) {
  const root = element.closest('.creative-stage');
  if (!root || !Observer) {ready(); return () => {};}
  let group = roots.get(root);
  if (!group) {
    const waiting = new Map();
    const observer = new Observer(entries => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        const callback = waiting.get(entry.target);
        if (callback) {waiting.delete(entry.target); observer.unobserve(entry.target); callback();}
      }
    }, {root, rootMargin:'120px', threshold:0});
    group = {waiting, observer};
    roots.set(root, group);
  }
  group.waiting.set(element, ready);
  group.observer.observe(element);
  return () => {
    group.waiting.delete(element);
    group.observer.unobserve(element);
    if (!group.waiting.size && roots.get(root) === group) {
      group.observer.disconnect(); roots.delete(root);
    }
  };
}
