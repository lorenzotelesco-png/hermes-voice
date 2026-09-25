import { render } from 'preact';
import '@fontsource/cormorant-garamond/300.css';
import '@fontsource/cormorant-garamond/400.css';
import '@fontsource/dm-sans/300.css';
import '@fontsource/dm-sans/400.css';
import './styles.css';
import { App } from './app';

render(<App />, document.getElementById('app')!);
