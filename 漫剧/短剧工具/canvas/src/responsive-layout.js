// Saved preferences are bounded for the current workspace, never overwritten on resize.
export function inspectorWidth(preferred,available) {
  const width=Number.isFinite(available)?available:1200;
  const min=width<850?260:300;
  const max=Math.max(min,Math.min(900,Math.floor(width*.4),width-480));
  return Math.round(Math.min(max,Math.max(min,Number.isFinite(preferred)?preferred:360)));
}

export function initialCanvasZoom(width,height,contentWidth=1800) {
  return Math.max(.4,Math.min(.85,(width-40)/contentWidth,height/650));
}
