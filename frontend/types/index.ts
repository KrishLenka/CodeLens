export interface User {
  username: string;
  avatar_url: string;
  free_uses_remaining: number;
  paid_uses_remaining: number;
}

export interface HistoryEntry {
  id: string;
  analyzed_at: string;
  repo_url: string;
  repo_name: string;
  overall_quality_score: number | null;
  recommendation: "strong_hire" | "hire" | "maybe" | "pass" | null;
  summary: string | null;
  had_resume: boolean;
  had_jd: boolean;
}

export interface Verdict {
  overall_quality_score: number | null;
  ai_usage_score: number | null;
  commit_health_score: number | null;
  resume_match_score: number | null;
  job_fit_score: number | null;
  strengths: string[];
  concerns: string[];
  skill_map: Record<string, "confirmed" | "partial" | "not_found">;
  vibe_coding_flags: string[];
  ai_usage_summary: string;
  bugs_found: string[];
  resume_inflation_flags: string[];
  job_fit_analysis: string | null;
  company_style_fit: number | null;
  recommendation: "strong_hire" | "hire" | "maybe" | "pass";
  recommendation_reasoning: string;
  summary: string;
  disclaimer: string;
}

export interface AnalysisResult {
  job_id: string;
  analysis_id?: string;
  verdict: Verdict;
  reports: Record<string, unknown>;
  resume_data: Record<string, unknown> | null;
  skill_matches: unknown[];
  project_matches: unknown[];
  undeclared_skills: string[];
  job_description: Record<string, unknown> | null;
  company_style_summary: string | null;
  indexed_chunks: number;
  candidate_namespace: string;
}

export interface JobStatus {
  job_id: string;
  username: string;
  status: "queued" | "running" | "done" | "error";
  progress: string;
  result?: AnalysisResult;
  error?: string;
  created_at: number;
  updated_at: number;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}
