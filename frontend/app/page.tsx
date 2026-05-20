"use client";

import { useEffect, useRef, useState } from "react";
import {
  confirmPayment,
  createCheckout,
  getHistory,
  getMe,
  loginUrl,
  logout,
  sendChatMessage,
  startAnalysis,
  waitForJob,
} from "@/lib/api";
import type {
  AnalysisResult,
  ChatMessage,
  HistoryEntry,
  User,
} from "@/types";

// ---------------------------------------------------------------------------
// Recommendation colours
// ---------------------------------------------------------------------------
const REC_STYLES: Record<string, { bg: string; text: string; label: string }> = {
  strong_hire: { bg: "#d9efdc", text: "#215732", label: "Strong Hire" },
  hire:        { bg: "#dce8f5", text: "#1a3263", label: "Hire" },
  maybe:       { bg: "#fef3cd", text: "#8a5a08", label: "Maybe" },
  pass:        { bg: "#fde8e8", text: "#8b1a1a", label: "Pass" },
};

function ScorePill({ score }: { score: number | null }) {
  const color = score == null ? "#888" : score >= 75 ? "#2e7d32" : score >= 50 ? "#e65100" : "#c62828";
  return (
    <span style={{ background: `${color}22`, color, fontWeight: 700, borderRadius: 999, padding: "2px 12px", fontSize: 14 }}>
      {score ?? "N/A"} / 100
    </span>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------
export default function Home() {
  const [user, setUser] = useState<User | null>(null);
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [result, setResult] = useState<AnalysisResult | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState("");
  const [error, setError] = useState<string | null>(null);

  // Form state
  const [githubUrl, setGithubUrl] = useState("");
  const [jobDescription, setJobDescription] = useState("");
  const [companyUrl, setCompanyUrl] = useState("");
  const [resume, setResume] = useState<File | null>(null);

  // Chat state
  const [chatHistory, setChatHistory] = useState<ChatMessage[]>([]);
  const [chatInput, setChatInput] = useState("");
  const [chatLoading, setChatLoading] = useState(false);

  // Auth + payment callback on load
  useEffect(() => {
    getMe().then(setUser).catch(() => setUser(null));

    const params = new URLSearchParams(window.location.search);
    const payment = params.get("payment");
    const gh_user = params.get("gh_user");
    const sid = params.get("sid");
    if (payment === "success" && gh_user && sid) {
      confirmPayment(gh_user, sid)
        .then(() => getMe().then(setUser))
        .catch(() => {});
      window.history.replaceState({}, "", "/");
    }
    if (params.get("auth_error")) {
      setError("GitHub sign-in failed. Please try again.");
      window.history.replaceState({}, "", "/");
    }
  }, []);

  useEffect(() => {
    if (user) getHistory().then(setHistory).catch(() => {});
  }, [user]);

  async function handleAnalyze(e: React.FormEvent) {
    e.preventDefault();
    if (!user) { setError("Please sign in first."); return; }
    if (!githubUrl.trim()) { setError("GitHub URL is required."); return; }

    setError(null);
    setRunning(true);
    setProgress("Starting...");
    setResult(null);

    try {
      const { job_id } = await startAnalysis({
        github_url: githubUrl,
        job_description: jobDescription,
        company_github_url: companyUrl,
        resume,
      });
      setJobId(job_id);
      const analysisResult = await waitForJob(job_id, setProgress);
      setResult(analysisResult);
      setChatHistory([]);
      // Refresh usage counts
      getMe().then(setUser).catch(() => {});
      getHistory().then(setHistory).catch(() => {});
    } catch (err: any) {
      if (err.message?.includes("402") || err.message?.includes("No analyses")) {
        // Offer to pay
        try {
          const { checkout_url } = await createCheckout({
            github_url: githubUrl,
            job_description: jobDescription,
            company_github_url: companyUrl,
          });
          window.location.href = checkout_url;
          return;
        } catch {}
      }
      setError(err.message || "Analysis failed. Please try again.");
    } finally {
      setRunning(false);
    }
  }

  async function handleChat(e: React.FormEvent) {
    e.preventDefault();
    if (!chatInput.trim() || !jobId) return;
    const userMsg: ChatMessage = { role: "user", content: chatInput };
    const newHistory = [...chatHistory, userMsg];
    setChatHistory(newHistory);
    setChatInput("");
    setChatLoading(true);
    try {
      const { response } = await sendChatMessage({
        job_id: jobId,
        message: userMsg.content,
        history: chatHistory,
      });
      setChatHistory([...newHistory, { role: "assistant", content: response }]);
    } catch {
      setChatHistory([...newHistory, { role: "assistant", content: "Sorry, I couldn't process that." }]);
    } finally {
      setChatLoading(false);
    }
  }

  const verdict = result?.verdict;
  const rec = verdict?.recommendation ? REC_STYLES[verdict.recommendation] : null;
  const totalUses = (user?.free_uses_remaining ?? 0) + (user?.paid_uses_remaining ?? 0);

  return (
    <div style={{ minHeight: "100vh", background: "#f4f6fa", fontFamily: "system-ui, sans-serif" }}>
      {/* Header */}
      <header style={{ background: "#1a3263", color: "#fff", padding: "0 32px", display: "flex", alignItems: "center", justifyContent: "space-between", height: 56 }}>
        <span style={{ fontWeight: 800, fontSize: 20, letterSpacing: -0.5 }}>
          <span style={{ color: "#FFC570" }}>Code</span>Lens
        </span>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          {user ? (
            <>
              <span style={{ fontSize: 13, color: "#8ea2bc" }}>
                {totalUses} {totalUses === 1 ? "analysis" : "analyses"} left
              </span>
              {user.avatar_url && (
                <img src={user.avatar_url} alt={user.username} style={{ width: 30, height: 30, borderRadius: "50%" }} />
              )}
              <span style={{ fontSize: 13 }}>@{user.username}</span>
              <button onClick={() => logout().then(() => { setUser(null); setResult(null); })}
                style={{ background: "transparent", border: "1px solid #fff3", color: "#fff", borderRadius: 8, padding: "4px 12px", cursor: "pointer", fontSize: 13 }}>
                Sign out
              </button>
            </>
          ) : (
            <a href={loginUrl()} style={{ background: "#FFC570", color: "#1a3263", fontWeight: 700, borderRadius: 8, padding: "6px 16px", textDecoration: "none", fontSize: 13 }}>
              Sign in with GitHub
            </a>
          )}
        </div>
      </header>

      <div style={{ maxWidth: 900, margin: "0 auto", padding: "32px 16px" }}>
        {/* Analysis form */}
        <form onSubmit={handleAnalyze} style={{ background: "#fff", borderRadius: 16, padding: 24, boxShadow: "0 2px 12px rgba(0,0,0,0.07)", marginBottom: 24 }}>
          <h2 style={{ margin: "0 0 16px", fontSize: 18, fontWeight: 700, color: "#1a3263" }}>Analyze a Repository</h2>
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <input
              type="url" placeholder="https://github.com/owner/repo"
              value={githubUrl} onChange={e => setGithubUrl(e.target.value)}
              disabled={running}
              required
              style={{ padding: "10px 14px", borderRadius: 8, border: "1px solid #d0d7e4", fontSize: 14, opacity: running ? 0.5 : 1 }}
            />
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
              <label style={{ fontSize: 13, color: "#555" }}>
                Resume (PDF/TXT, optional)
                <input type="file" accept=".pdf,.txt" disabled={running}
                  onChange={e => setResume(e.target.files?.[0] ?? null)}
                  style={{ display: "block", marginTop: 4 }} />
              </label>
              <textarea
                placeholder="Paste job description (optional)..."
                value={jobDescription} onChange={e => setJobDescription(e.target.value)}
                disabled={running} rows={3}
                style={{ padding: "10px 14px", borderRadius: 8, border: "1px solid #d0d7e4", fontSize: 13, resize: "vertical", opacity: running ? 0.5 : 1 }}
              />
            </div>
            <input
              type="url" placeholder="Company GitHub URL (optional)"
              value={companyUrl} onChange={e => setCompanyUrl(e.target.value)}
              disabled={running}
              style={{ padding: "10px 14px", borderRadius: 8, border: "1px solid #d0d7e4", fontSize: 14, opacity: running ? 0.5 : 1 }}
            />
          </div>
          {error && <p style={{ color: "#c62828", margin: "12px 0 0", fontSize: 13 }}>{error}</p>}
          <button type="submit" disabled={running || !user}
            style={{ marginTop: 16, width: "100%", padding: "12px", borderRadius: 8, background: running ? "#8ea2bc" : "#1a3263", color: "#fff", fontWeight: 700, fontSize: 15, border: "none", cursor: running ? "not-allowed" : "pointer" }}>
            {running ? `⟳ ${progress}` : "Analyze Repository"}
          </button>
          {!user && <p style={{ textAlign: "center", color: "#888", fontSize: 13, margin: "8px 0 0" }}>Sign in with GitHub to run an analysis</p>}
        </form>

        {/* Results */}
        {verdict && (
          <div style={{ background: "#fff", borderRadius: 16, padding: 24, boxShadow: "0 2px 12px rgba(0,0,0,0.07)", marginBottom: 24 }}>
            <h2 style={{ margin: "0 0 20px", fontSize: 18, fontWeight: 700, color: "#1a3263" }}>Analysis Results</h2>

            {/* Scores */}
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: 12, marginBottom: 20 }}>
              {[
                ["Overall Quality", verdict.overall_quality_score],
                ["Code Quality", (result?.reports as any)?.code_quality?.quality_score ?? null],
                ["Commit Health", verdict.commit_health_score],
                ["AI Usage", verdict.ai_usage_score],
                ...(verdict.resume_match_score != null ? [["Resume Match", verdict.resume_match_score]] : []),
                ...(verdict.job_fit_score != null ? [["Job Fit", verdict.job_fit_score]] : []),
              ].map(([label, score]) => (
                <div key={label as string} style={{ background: "#f4f6fa", borderRadius: 12, padding: "14px 12px", textAlign: "center" }}>
                  <div style={{ fontSize: 11, color: "#888", fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 6 }}>{label as string}</div>
                  <ScorePill score={score as number | null} />
                </div>
              ))}
            </div>

            {/* Recommendation */}
            {rec && (
              <div style={{ background: rec.bg, borderRadius: 12, padding: "16px 20px", marginBottom: 20 }}>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
                  <span style={{ fontWeight: 700, color: rec.text, fontSize: 13, textTransform: "uppercase", letterSpacing: "0.06em" }}>Recommendation</span>
                  <span style={{ background: rec.text, color: rec.bg, fontWeight: 800, borderRadius: 999, padding: "3px 14px", fontSize: 13 }}>{rec.label}</span>
                </div>
                <p style={{ margin: 0, color: "#3a4a5c", fontSize: 13, lineHeight: 1.7 }}>{verdict.summary}</p>
                {verdict.recommendation_reasoning && verdict.recommendation_reasoning !== verdict.summary && (
                  <p style={{ margin: "10px 0 0", color: "#3a4a5c", fontSize: 13, lineHeight: 1.7 }}>{verdict.recommendation_reasoning}</p>
                )}
              </div>
            )}

            {/* Strengths & Concerns */}
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginBottom: 20 }}>
              <div>
                <h3 style={{ fontSize: 13, fontWeight: 700, color: "#2e7d32", marginBottom: 8, textTransform: "uppercase", letterSpacing: "0.06em" }}>Strengths</h3>
                <ul style={{ margin: 0, paddingLeft: 18 }}>
                  {verdict.strengths.map((s, i) => <li key={i} style={{ fontSize: 13, color: "#333", marginBottom: 4 }}>{s}</li>)}
                </ul>
              </div>
              <div>
                <h3 style={{ fontSize: 13, fontWeight: 700, color: "#c62828", marginBottom: 8, textTransform: "uppercase", letterSpacing: "0.06em" }}>Concerns</h3>
                <ul style={{ margin: 0, paddingLeft: 18 }}>
                  {verdict.concerns.map((c, i) => <li key={i} style={{ fontSize: 13, color: "#333", marginBottom: 4 }}>{c}</li>)}
                </ul>
              </div>
            </div>

            {/* Skill map */}
            {Object.keys(verdict.skill_map ?? {}).length > 0 && (
              <div style={{ marginBottom: 20 }}>
                <h3 style={{ fontSize: 13, fontWeight: 700, color: "#1a3263", marginBottom: 10, textTransform: "uppercase", letterSpacing: "0.06em" }}>Skill Map</h3>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                  {Object.entries(verdict.skill_map).map(([skill, verdict_]) => {
                    const color = verdict_ === "confirmed" ? "#2e7d32" : verdict_ === "partial" ? "#e65100" : "#888";
                    return (
                      <span key={skill} style={{ background: `${color}22`, color, fontWeight: 600, borderRadius: 999, padding: "3px 12px", fontSize: 12 }}>
                        {skill} · {verdict_}
                      </span>
                    );
                  })}
                </div>
              </div>
            )}

            {/* Disclaimer */}
            <p style={{ fontSize: 11, color: "#888", margin: 0, fontStyle: "italic" }}>{verdict.disclaimer}</p>
          </div>
        )}

        {/* Chat */}
        {result && jobId && (
          <div style={{ background: "#fff", borderRadius: 16, padding: 24, boxShadow: "0 2px 12px rgba(0,0,0,0.07)", marginBottom: 24 }}>
            <h2 style={{ margin: "0 0 16px", fontSize: 18, fontWeight: 700, color: "#1a3263" }}>Ask about this repo</h2>
            <div style={{ maxHeight: 300, overflowY: "auto", marginBottom: 12, display: "flex", flexDirection: "column", gap: 8 }}>
              {chatHistory.map((msg, i) => (
                <div key={i} style={{ alignSelf: msg.role === "user" ? "flex-end" : "flex-start", background: msg.role === "user" ? "#1a3263" : "#f4f6fa", color: msg.role === "user" ? "#fff" : "#333", borderRadius: 12, padding: "8px 14px", fontSize: 13, maxWidth: "80%" }}>
                  {msg.content}
                </div>
              ))}
              {chatLoading && <div style={{ alignSelf: "flex-start", color: "#888", fontSize: 13 }}>Thinking…</div>}
            </div>
            <form onSubmit={handleChat} style={{ display: "flex", gap: 8 }}>
              <input
                value={chatInput} onChange={e => setChatInput(e.target.value)}
                placeholder="Ask anything about this candidate's code..."
                style={{ flex: 1, padding: "10px 14px", borderRadius: 8, border: "1px solid #d0d7e4", fontSize: 13 }}
              />
              <button type="submit" disabled={chatLoading || !chatInput.trim()}
                style={{ padding: "10px 20px", borderRadius: 8, background: "#1a3263", color: "#fff", fontWeight: 700, border: "none", cursor: "pointer" }}>
                Send
              </button>
            </form>
          </div>
        )}

        {/* History */}
        {user && history.length > 0 && (
          <div style={{ background: "#fff", borderRadius: 16, padding: 24, boxShadow: "0 2px 12px rgba(0,0,0,0.07)" }}>
            <h2 style={{ margin: "0 0 16px", fontSize: 18, fontWeight: 700, color: "#1a3263" }}>Recent Analyses</h2>
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {history.slice(0, 5).map(entry => {
                const r = entry.recommendation ? REC_STYLES[entry.recommendation] : null;
                return (
                  <div key={entry.id} style={{ display: "flex", alignItems: "center", gap: 12, padding: "12px 16px", background: "#f4f6fa", borderRadius: 12 }}>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontWeight: 700, fontSize: 14, color: "#1a3263", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{entry.repo_name}</div>
                      <div style={{ fontSize: 12, color: "#888" }}>{entry.analyzed_at?.slice(0, 10)}</div>
                    </div>
                    <ScorePill score={entry.overall_quality_score} />
                    {r && <span style={{ background: r.bg, color: r.text, fontWeight: 700, borderRadius: 999, padding: "2px 10px", fontSize: 12 }}>{r.label}</span>}
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
