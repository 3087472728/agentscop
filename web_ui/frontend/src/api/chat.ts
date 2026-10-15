import { basicAuthHeader, client, getBaseUrl } from './client';
import type { ChatRequest } from './types';

/** Upload response of `POST /chat_uploads/`. */
export interface ChatImageUpload {
	/** Relative URL for inline reference, e.g. `/chat_uploads/<uuid>.png`. */
	url: string;
	media_type: string;
	name: string;
}

/**
 * Chat API — fire-and-forget trigger for chat runs.
 *
 * Events produced by the run are delivered via the session's SSE
 * stream endpoint (``GET /sessions/{sid}/stream``), not in the
 * response body of this POST.
 */
export const chatApi = {
	/**
	 * Trigger a chat run for the specified session.
	 *
	 * Accepts user messages, human-in-the-loop confirmation events,
	 * or ``null`` (continue from current state). Returns immediately;
	 * the caller should already be subscribed to the session's SSE
	 * stream to receive the resulting events.
	 *
	 * @param body - The chat request payload.
	 * @returns A confirmation object ``{ status, session_id }``.
	 */
	trigger: (body: ChatRequest) =>
		client.post<{ status: string; session_id: string }>('/chat/', body),

	/**
	 * Upload one chat image to the backend (multipart). The message
	 * then references it by URL instead of inlining megabytes of
	 * base64 into the payload / SSE stream.
	 *
	 * @param file - An image file (`image/*` MIME types only).
	 * @returns The stored blob's relative URL and metadata.
	 */
	uploadImage: async (file: File): Promise<ChatImageUpload> => {
		const form = new FormData();
		form.append('file', file);
		const res = await fetch(new URL('/chat_uploads/', getBaseUrl()), {
			method: 'POST',
			headers: basicAuthHeader(),
			body: form,
		});
		if (!res.ok) {
			const text = await res.text().catch(() => '');
			throw new Error(
				`Image upload failed (${res.status}): ${text || res.statusText}`,
			);
		}
		return res.json() as Promise<ChatImageUpload>;
	},
};
