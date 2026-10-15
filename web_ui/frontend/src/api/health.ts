import { client } from './client';
import type { HealthResponse } from './types';

/** The probe is I/O-free on the server, so anything this slow is a stalled backend. */
const HEALTH_TIMEOUT_MS = 10_000;

export const healthApi = {
	/**
	 * Probe a backend before its address and credentials are persisted,
	 * which is why they are passed explicitly instead of read from
	 * localStorage.
	 */
	check: (baseUrl: string, userId: string, password: string) =>
		client.get<HealthResponse>('/health', undefined, {
			silent: true,
			baseUrl,
			userId,
			password,
			timeoutMs: HEALTH_TIMEOUT_MS,
		}),
};
