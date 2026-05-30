// 认证相关的 API 调用与 token 持久化
// token 存 sessionStorage（关标签页即清除）而不是 localStorage，
// 避免 token 在浏览器历史里残留太久。

const TOKEN_KEY = "pr_review_token";

export interface AuthUser {
  user_id: number;
  username: string;
  github_username: string | null;
  has_pat: boolean;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  user_id: number;
  username: string;
}

export function saveToken(token: string): void {
  sessionStorage.setItem(TOKEN_KEY, token);
}

export function loadToken(): string | null {
  return sessionStorage.getItem(TOKEN_KEY);
}

export function clearToken(): void {
  sessionStorage.removeItem(TOKEN_KEY);
}

export function authHeaders(): Record<string, string> {
  const token = loadToken();
  if (!token) return {};
  return { Authorization: `Bearer ${token}` };
}

export async function apiRegister(username: string, password: string): Promise<TokenResponse> {
  const resp = await fetch("/api/auth/register", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  const data = await resp.json();
  if (!resp.ok) {
    throw new Error(String(data.detail ?? `注册失败 HTTP ${resp.status}`));
  }
  return data as TokenResponse;
}

export async function apiLogin(username: string, password: string): Promise<TokenResponse> {
  const resp = await fetch("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  const data = await resp.json();
  if (!resp.ok) {
    throw new Error(String(data.detail ?? `登录失败 HTTP ${resp.status}`));
  }
  return data as TokenResponse;
}

export async function apiGetMe(): Promise<AuthUser> {
  const resp = await fetch("/api/user/me", {
    headers: authHeaders(),
  });
  if (!resp.ok) throw new Error("未登录");
  return (await resp.json()) as AuthUser;
}

export async function apiBindPAT(pat: string): Promise<{ ok: boolean; github_username: string }> {
  const resp = await fetch("/api/user/github-pat", {
    method: "PUT",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ pat }),
  });
  const data = await resp.json();
  if (!resp.ok) {
    throw new Error(String(data.detail ?? `绑定失败 HTTP ${resp.status}`));
  }
  return data as { ok: boolean; github_username: string };
}

export async function apiUnbindPAT(): Promise<void> {
  const resp = await fetch("/api/user/github-pat", {
    method: "DELETE",
    headers: authHeaders(),
  });
  if (!resp.ok) {
    const data = await resp.json().catch(() => ({}));
    throw new Error(String((data as { detail?: string }).detail ?? `解绑失败 HTTP ${resp.status}`));
  }
}

export interface FixResponse {
  status: string;
  patch: string | null;
  review_verdict: string;
  pr_url: string | null;
  error: string | null;
}

export async function apiFixFinding(reviewId: string, findingIndex: number): Promise<FixResponse> {
  const resp = await fetch(`/api/review/${reviewId}/fix/${findingIndex}`, {
    method: "POST",
    headers: authHeaders(),
  });
  const data = await resp.json();
  if (!resp.ok) {
    throw new Error(String(data.detail ?? `修复失败 HTTP ${resp.status}`));
  }
  return data as FixResponse;
}
