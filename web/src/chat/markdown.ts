import { micromark } from 'micromark';
import { gfm, gfmHtml } from 'micromark-extension-gfm';

// micromark is safe by default, which is why it is used here: raw HTML in the
// text is escaped and javascript:/data: links are dropped. Replies are written
// by a model that reads web pages and, from phase 4, other people's messages,
// so anything it writes is untrusted as markup.
export function renderMarkdown(text: string) {
  return micromark(text, { extensions: [gfm()], htmlExtensions: [gfmHtml()] });
}
