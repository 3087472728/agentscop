// Temp test: SSR-render the exported page-note body; assert math rendering.
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { readFileSync } from 'node:fs';
import { Streamdown } from 'streamdown';
import { cjk } from '@streamdown/cjk';
import { code } from '@streamdown/code';
import { createMathPlugin } from '@streamdown/math';
import { mermaid } from '@streamdown/mermaid';

const math = createMathPlugin({ singleDollarTextMath: true });
const plugins = { code, math, cjk, mermaid };

const md = readFileSync(
  'D:/codespace/agentscope_workspace/agentscope_extra/logs/_note_body.md',
  'utf-8'
);
const html = renderToStaticMarkup(
  React.createElement(Streamdown, { plugins }, md)
);

const display = (html.match(/katex-display/g) || []).length;
const errors = (html.match(/katex-error/g) || []).length;
const inline = (html.match(/class="katex"/g) || []).length;
console.log(
  `SSR: katex-display=${display} katex-error=${errors} inline-katex=${inline}`
);
if (display === 0 || errors > 0 || inline === 0) {
  console.log('SSR RENDER FAILED');
  process.exit(1);
}
console.log('SSR RENDER OK (display math + inline math rendered, no errors)');
