import { cjk } from '@streamdown/cjk';
import { code } from '@streamdown/code';
import { createMathPlugin } from '@streamdown/math';
import { mermaid } from '@streamdown/mermaid';
import * as React from 'react';
import { Streamdown } from 'streamdown';

import { cn } from '@/lib/utils';

import 'katex/dist/katex.min.css';

// Inline single-$ math ($K_m$) is rendered as well as $$…$$ blocks.
// Streamdown disables it by default to avoid clashing with currency
// amounts — irrelevant for a Chinese engineering-spec knowledge base.
const math = createMathPlugin({ singleDollarTextMath: true });
const DEFAULT_PLUGINS = { code, math, cjk, mermaid };

function Markdown({
	className,
	plugins = DEFAULT_PLUGINS,
	controls = false,
	...props
}: React.ComponentProps<typeof Streamdown>) {
	return (
		<Streamdown
			data-slot="markdown"
			plugins={plugins}
			controls={controls}
			className={cn('cn-markdown w-full min-w-0 overflow-hidden', className)}
			{...props}
		/>
	);
}

export { Markdown };
