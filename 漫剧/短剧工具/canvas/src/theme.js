export function readTheme(storage) {
  try {
    const value=storage.getItem('manju.studio.theme');
    if (value==='dark' || value==='light') return value;
    return storage.getItem('manju.canvas.background')==='dark'?'dark':'light';
  } catch {return 'light';}
}

export function applyTheme(theme) {
  document.documentElement.dataset.theme=theme;
  try {localStorage.setItem('manju.studio.theme',theme);}catch{/* Theme still works when storage is unavailable. */}
}
