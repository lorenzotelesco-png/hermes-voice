import { render } from 'preact';
import '@fontsource/cormorant-garamond/300.css';
import '@fontsource/cormorant-garamond/400.css';
import '@fontsource/dm-sans/300.css';
import '@fontsource/dm-sans/400.css';
import './styles.css';
import { App } from './app';
import { registerWorker } from './lib/push';

render(<App />, document.getElementById('app')!);
// Push notifications only; it caches nothing (see public/sw.js).
registerWorker();
