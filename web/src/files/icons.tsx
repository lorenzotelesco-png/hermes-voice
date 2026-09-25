import { isImage, isNote, isPdf } from './vault';

const P = {
  folder: <path d="M4 6.5A1.5 1.5 0 0 1 5.5 5H10l2 2h6.5A1.5 1.5 0 0 1 20 8.5v9a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 17.5z" />,
  note: <g><path d="M7 4h7l4 4v12H7z" /><path d="M14 4v4h4M9.5 12h6M9.5 15.5h6" /></g>,
  image: <g><rect x="4" y="5" width="16" height="14" rx="2" /><circle cx="9" cy="10" r="1.5" /><path d="M20 16l-5-5-8 8" /></g>,
  pdf: <g><path d="M7 4h7l4 4v12H7z" /><path d="M14 4v4h4" /><text x="8.4" y="17.2" font-size="5.2" stroke="none" fill="currentColor" font-family="system-ui">PDF</text></g>,
  file: <g><path d="M7 4h7l4 4v12H7z" /><path d="M14 4v4h4" /></g>,
  back: <path d="M15 5l-7 7 7 7" />,
  edit: <path d="M5 19h4L19 9l-4-4L5 15zM13 7l4 4" />,
  plus: <path d="M12 5v14M5 12h14" />,
  sync: <path d="M20 12a8 8 0 1 1-2.3-5.6M20 4v5h-5" />,
  mic: <g><rect x="9" y="3.5" width="6" height="11" rx="3" /><path d="M6 11.5a6 6 0 0 0 12 0M12 17.5V21" /></g>,
  stop: <rect x="7" y="7" width="10" height="10" rx="2" />,
  attach: <path d="M16.5 8.5l-6.8 6.8a2 2 0 0 1-2.8-2.8l7.4-7.4a3.5 3.5 0 0 1 5 5l-7.6 7.6a5 5 0 0 1-7.1-7.1L11 4.2" />,
  send: <path d="M5 12h13M13 6l6 6-6 6" />,
};

export type IconName = keyof typeof P;

export function Icon({ name }: { name: IconName }) {
  return <svg class="ico" viewBox="0 0 24 24" aria-hidden="true">{P[name]}</svg>;
}

export function fileIcon(path: string): IconName {
  return isNote(path) ? 'note' : isImage(path) ? 'image' : isPdf(path) ? 'pdf' : 'file';
}
