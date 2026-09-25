// Back inside the File tab goes where you came from (search, a folder, a
// linked note), not to a fixed parent; a page opened cold falls back to one.
const trail: string[] = [];

export function remember(hash: string) {
  if (trail[trail.length - 1] !== hash) trail.push(hash);
  if (trail.length > 60) trail.shift();
}

export function goBack(fallback: string) {
  trail.pop();
  location.replace(trail.pop() || fallback);
}

/** Leave the current page for `hash` without leaving it behind in the trail (the editor after saving). */
export function leaveTo(hash: string) {
  trail.pop();
  if (trail[trail.length - 1] === hash) trail.pop();
  location.replace(hash);
}

// One line for the next page to show: "saved", "merged with the PC's edit"...
let pending: string | null = null;
export function flashNext(msg: string) { pending = msg || null; }
export function takeFlash() { const m = pending; pending = null; return m; }
