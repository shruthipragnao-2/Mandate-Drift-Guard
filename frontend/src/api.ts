// Thin fetch wrapper -- plain fetch + useState is enough for 3 screens and no client-side
// caching/mutation complexity (Ops-analyst tool, single-user-role, no need for a data-fetching
// library). Every request carries the single shared bearer token (Decision 17); there is no
// login flow to obtain one.

import type {
  CaseDetailResponse,
  CaseListResponse,
  CaseState,
  ResolveRequest,
  ResolveResponse,
  TransactionCreateRequest,
  TransactionCreateResponse,
} from "./types";

const BASE_URL = import.meta.env.VITE_API_BASE_URL as string;
const BEARER_TOKEN = import.meta.env.VITE_API_BEARER_TOKEN as string;

export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, detail: unknown) {
    super(`API error ${status}: ${JSON.stringify(detail)}`);
    this.status = status;
    this.detail = detail;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${BEARER_TOKEN}`,
      ...init?.headers,
    },
  });

  if (!response.ok) {
    let detail: unknown;
    try {
      detail = await response.json();
    } catch {
      detail = await response.text();
    }
    throw new ApiError(response.status, detail);
  }

  return (await response.json()) as T;
}

export function listCases(state: CaseState = "hold"): Promise<CaseListResponse> {
  return request<CaseListResponse>(`/cases?state=${state}`);
}

export function getCaseDetail(caseId: string): Promise<CaseDetailResponse> {
  return request<CaseDetailResponse>(`/cases/${caseId}`);
}

export function resolveCase(caseId: string, body: ResolveRequest): Promise<ResolveResponse> {
  return request<ResolveResponse>(`/cases/${caseId}/resolve`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function createTransaction(
  body: TransactionCreateRequest,
): Promise<TransactionCreateResponse> {
  return request<TransactionCreateResponse>("/transactions", {
    method: "POST",
    body: JSON.stringify(body),
  });
}
