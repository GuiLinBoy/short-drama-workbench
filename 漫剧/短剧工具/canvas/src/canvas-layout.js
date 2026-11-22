const collator = new Intl.Collator('zh-CN', { numeric: true });
const KIND_ORDER = { project: 0, asset: 1, image: 1, story: 2, review: 2, video: 3, audio: 4, final: 5 };
const BASE_LAYER = { project: 0, asset: 0, image: 1, story: 1, review: 1, video: 2, audio: 3, final: 4 };

export function canvasColumn(node) {
  const {kind,record={},title=''}=node.data || {};
  if (kind==='asset') {
    if (['character','prop'].includes(record.type)) return 0;
    return record.source_asset_id || /关键帧|关键构图|构图参考/.test(`${record.name || ''} ${title}`) ? 1 : 0;
  }
  return BASE_LAYER[kind] ?? 0;
}
const finiteSize = (value, fallback) => Number.isFinite(value) && value > 0 && value < 8000 ? value : fallback;

export function nodeDimensions(node) {
  const kind = node.data?.kind;
  const heights = { project: 150, asset: node.data?.preview?.exists ? 370 : 210,
    image: node.data?.preview?.exists ? 370 : 210, story: 270, video: 330, audio: 230, final: 230 };
  return { width: finiteSize(node.measured?.width ?? node.width, 320),
    height: finiteSize(node.measured?.height ?? node.height, heights[kind] || 200) };
}

export function layoutSnapshot(nodes) {
  return Object.fromEntries(nodes.map(node => [node.id, { x: node.position.x, y: node.position.y }]));
}

// Stable Kahn ordering. Break a cycle only for display, preserving every source edge.
function topologicalOrder(ids, links, compare) {
  const pending = new Set(ids), incoming = new Map(ids.map(id => [id, 0]));
  const outgoing = new Map(ids.map(id => [id, []]));
  const seen = new Set();
  for (const { source, target } of links) {
    if (!pending.has(source) || !pending.has(target) || source === target) continue;
    const key = JSON.stringify([source, target]);
    if (seen.has(key)) continue;
    seen.add(key);
    outgoing.get(source).push(target);
    incoming.set(target, incoming.get(target) + 1);
  }
  const ready = ids.filter(id => incoming.get(id) === 0), order = [];
  while (pending.size) {
    if (!ready.length) ready.push([...pending].sort(compare)[0]);
    ready.sort(compare);
    const id = ready.shift();
    if (!pending.delete(id)) continue;
    order.push(id);
    for (const target of outgoing.get(id)) {
      incoming.set(target, incoming.get(target) - 1);
      if (pending.has(target) && incoming.get(target) <= 0) ready.push(target);
    }
  }
  return order;
}

// Geometry-only auto layout: no manifest edits, API calls, or external dependencies.
export function arrangeCanvas(nodes, edges, { padding = 40, columnGap = 400, rowGap = 52 } = {}) {
  if (!nodes.length) return [];
  const byId = new Map(nodes.map(node => [node.id, node]));
  const dimensions = new Map(nodes.map(node => [node.id, nodeDimensions(node)]));
  const stable = (a, b) => (KIND_ORDER[byId.get(a).data?.kind] ?? 6) -
    (KIND_ORDER[byId.get(b).data?.kind] ?? 6) || collator.compare(a, b);
  const valid = edges.filter(edge => byId.has(edge.source) && byId.has(edge.target));
  const columns = new Map();
  for (const node of nodes) {
    const rank = canvasColumn(node);
    if (!columns.has(rank)) columns.set(rank, []);
    columns.get(rank).push(node.id);
  }
  const positions = new Map();
  let x = padding;
  for (const rank of [...columns.keys()].sort((a, b) => a - b)) {
    const ids = columns.get(rank), sameColumn = new Set(ids);
    const inColumn = valid.filter(edge => sameColumn.has(edge.source) && sameColumn.has(edge.target));
    const rows = topologicalOrder(ids, inColumn, stable);
    let bottom = padding - rowGap;
    for (const id of rows) {
      const size = dimensions.get(id);
      const y = bottom + rowGap;
      positions.set(id, { x, y });
      bottom = y + size.height;
    }
    x += Math.max(...ids.map(id => dimensions.get(id).width)) + columnGap;
  }
  return nodes.map(node => ({ ...node, position: positions.get(node.id) }));
}
