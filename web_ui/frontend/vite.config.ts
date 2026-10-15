import path from 'path';

import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';
import svgr from 'vite-plugin-svgr';

export default defineConfig({
	plugins: [react(), tailwindcss(), svgr()],
	server: {
		// Listen on all interfaces so LAN clients can reach the dev
		// server via the host IP (e.g. http://192.168.100.2:5173).
		host: true,
		// Docker bind mounts on Windows/WSL2 don't propagate inotify
		// events; set VITE_USE_POLLING=1 to keep HMR working there.
		watch:
			process.env.VITE_USE_POLLING === '1'
				? { usePolling: true, interval: 300 }
				: undefined,
		proxy: {
			// 直接打开 5173 时把 API 转给后端；单端口模式(经 8020 访问)不走此代理
			'/api': `http://localhost:${process.env.VITE_API_PORT || 8020}`,
		},
	},
	resolve: {
		alias: {
			'@': path.resolve(__dirname, './src'),
			'next/navigation': path.resolve(__dirname, './src/lib/next-navigation-shim.ts'),
		},
	},
	optimizeDeps: {
		include: ['mime-types'],
	},
});
