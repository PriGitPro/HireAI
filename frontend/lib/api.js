/**
 * API client for the Intelligent Hiring Copilot backend.
 *
 * Includes SSE (Server-Sent Events) support for streaming evaluation progress.
 */

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000/api/v1';

class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

async function request(path, options = {}) {
  const url = `${API_BASE}${path}`;
  const config = {
    headers: {
      ...(options.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
      ...options.headers,
    },
    ...options,
  };

  if (options.body && !(options.body instanceof FormData)) {
    config.body = JSON.stringify(options.body);
  }

  try {
    const response = await fetch(url, config);
    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new ApiError(errorData.detail || `Request failed (${response.status})`, response.status);
    }
    if (response.status === 204) return null;
    return await response.json();
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new ApiError(`Network error: ${error.message}`, 0);
  }
}

// ── Requisitions ────────────────────────────────────────────────────────

export async function getRequisitions(status = null) {
  const params = status ? `?status=${status}` : '';
  return request(`/requisitions${params}`);
}

export async function getRequisition(id) {
  return request(`/requisitions/${id}`);
}

export async function createRequisition(data) {
  return request('/requisitions', {
    method: 'POST',
    body: data,
  });
}

export async function deleteRequisition(id) {
  return request(`/requisitions/${id}`, { method: 'DELETE' });
}

// ── Candidates ──────────────────────────────────────────────────────────

export async function getCandidates(reqId) {
  return request(`/requisitions/${reqId}/candidates`);
}

export async function getCandidate(reqId, candidateId) {
  return request(`/requisitions/${reqId}/candidates/${candidateId}`);
}

export async function createCandidate(reqId, formData) {
  return request(`/requisitions/${reqId}/candidates`, {
    method: 'POST',
    body: formData,
    headers: {},
  });
}

export async function deleteCandidate(reqId, candidateId) {
  return request(`/requisitions/${reqId}/candidates/${candidateId}`, { method: 'DELETE' });
}

// ── SSE Streaming Evaluation ────────────────────────────────────────────

/**
 * Open an SSE connection for streaming evaluation progress.
 *
 * @param {string} reqId         - Requisition ID
 * @param {string} candidateId   - Candidate ID
 * @param {object} callbacks     - Event handlers:
 *   onStage(data)    — Pipeline stage progress {stage, step, total_steps, message, ...}
 *   onCached(data)   — Returning cached evaluation {evaluation}
 *   onResult(data)   — Final evaluation result {evaluation, candidate_status}
 *   onError(data)    — Error occurred {message}
 *   onDone(data)     — Stream complete {total_time_ms}
 * @param {boolean} force        - Force re-evaluation
 * @returns {EventSource} - The EventSource instance (call .close() to cancel)
 */
export function evaluateCandidateSSE(reqId, candidateId, callbacks = {}, force = false) {
  const url = `${API_BASE}/requisitions/${reqId}/candidates/${candidateId}/evaluate/stream?force=${force}`;

  const eventSource = new EventSource(url);

  eventSource.addEventListener('stage', (e) => {
    const data = JSON.parse(e.data);
    callbacks.onStage?.(data);
  });

  eventSource.addEventListener('cached', (e) => {
    const data = JSON.parse(e.data);
    callbacks.onCached?.(data);
  });

  eventSource.addEventListener('result', (e) => {
    const data = JSON.parse(e.data);
    callbacks.onResult?.(data);
  });

  eventSource.addEventListener('error', (e) => {
    // EventSource fires 'error' both for SSE errors and connection issues
    if (e.data) {
      const data = JSON.parse(e.data);
      callbacks.onError?.(data);
    } else {
      // Connection error
      callbacks.onError?.({ message: 'Connection to evaluation stream lost' });
    }
  });

  eventSource.addEventListener('done', (e) => {
    const data = JSON.parse(e.data);
    callbacks.onDone?.(data);
    eventSource.close(); // Close connection after done
  });

  // Handle connection errors
  eventSource.onerror = (e) => {
    if (eventSource.readyState === EventSource.CLOSED) {
      return; // Normal close
    }
    callbacks.onError?.({ message: 'SSE connection error' });
    eventSource.close();
  };

  return eventSource;
}

// ── Evaluation (sync fallback) ──────────────────────────────────────────

export async function evaluateCandidate(reqId, candidateId, forceReevaluate = false) {
  return request(`/requisitions/${reqId}/candidates/${candidateId}/evaluate`, {
    method: 'POST',
    body: { force_reevaluate: forceReevaluate },
  });
}

export async function overrideEvaluation(reqId, candidateId, data) {
  return request(`/requisitions/${reqId}/candidates/${candidateId}/override`, {
    method: 'POST',
    body: data,
  });
}

// ── Audit ───────────────────────────────────────────────────────────────

export async function getAuditLog(reqId, candidateId) {
  return request(`/requisitions/${reqId}/candidates/${candidateId}/audit`);
}

// ── Dashboard ───────────────────────────────────────────────────────────

export async function getDashboardStats() {
  return request('/dashboard/stats');
}

export async function getHealthCheck() {
  return request('/dashboard/health');
}
