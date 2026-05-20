import type { AnalysisResult, ChatMessage, HistoryEntry, JobStatus, User } from "@/types";

const BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    credentials: "include",
    ...init,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

// Auth
export const getMe = () => request<User>("/api/auth/me");
export const logout = () => request<{ status: string }>("/api/auth/logout", { method: "POST" });
export const loginUrl = () => `${BASE}/api/auth/login`;

// Analysis
export async function startAnalysis(params: {
  github_url: string;
  job_description?: string;
  company_github_url?: string;
  resume?: File | null;
}): Promise<{ job_id: string; status: string }> {
  const form = new FormData();
  form.append("github_url", params.github_url);
  form.append("job_description", params.job_description ?? "");
  form.append("company_github_url", params.company_github_url ?? "");
  if (params.resume) form.append("resume", params.resume);
  return request("/api/analyze", { method: "POST", body: form });
}

export const pollJob = (jobId: string) => request<JobStatus>(`/api/analyze/${jobId}`);

export async function waitForJob(
  jobId: string,
  onProgress: (msg: string) => void,
  intervalMs = 2500
): Promise<AnalysisResult> {
  return new Promise((resolve, reject) => {
    const interval = setInterval(async () => {
      try {
        const job = await pollJob(jobId);
        onProgress(job.progress);
        if (job.status === "done" && job.result) {
          clearInterval(interval);
          resolve(job.result);
        } else if (job.status === "error") {
          clearInterval(interval);
          reject(new Error(job.error || "Analysis failed"));
        }
      } catch (err) {
        clearInterval(interval);
        reject(err);
      }
    }, intervalMs);
  });
}

// History
export const getHistory = () => request<HistoryEntry[]>("/api/history");
export const getHistoryEntry = (id: string) => request<{ result: AnalysisResult } & HistoryEntry>(`/api/history/${id}`);

// Chat
export async function sendChatMessage(params: {
  job_id?: string;
  analysis_id?: string;
  message: string;
  history: ChatMessage[];
}): Promise<{ response: string }> {
  return request("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
}

// Payment
export async function createCheckout(params: {
  github_url?: string;
  job_description?: string;
  company_github_url?: string;
}): Promise<{ checkout_url: string }> {
  const form = new FormData();
  form.append("github_url", params.github_url ?? "");
  form.append("job_description", params.job_description ?? "");
  form.append("company_github_url", params.company_github_url ?? "");
  return request("/api/payment/checkout", { method: "POST", body: form });
}

export async function confirmPayment(gh_user: string, sid: string) {
  const form = new FormData();
  form.append("gh_user", gh_user);
  form.append("sid", sid);
  return request("/api/payment/confirm", { method: "POST", body: form });
}
