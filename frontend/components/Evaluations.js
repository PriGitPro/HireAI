'use client';

import { useState, useEffect, useRef, useCallback } from 'react';
import {
  getCandidates,
  getCandidate,
  createCandidate,
  evaluateCandidateSSE,
  overrideEvaluation,
  deleteCandidate,
  getAuditLog,
} from '../lib/api';

// ── Pipeline stage config (aligned with signal-driven backend) ──────────────

const PIPELINE_STAGES = [
  { key: 'loading',        label: 'Loading Data',       icon: '📂' },
  { key: 'jd_parsing',     label: 'Parsing JD',         icon: '📋' },
  { key: 'resume_parsing', label: 'Analyzing Resume',   icon: '📄' },
  { key: 'matching',       label: 'Skill Matching',     icon: '🎯' },
  { key: 'deciding',       label: 'Decision Engine',    icon: '⚖️' },
  { key: 'validating',     label: 'Validating Output',  icon: '✔️' },
  { key: 'saving',         label: 'Saving Results',     icon: '💾' },
];

function getStageIndex(stageKey) {
  const map = {
    loading: 0, loaded: 0,
    jd_parsing: 1, jd_parsed: 1,
    resume_parsing: 2, resume_parsed: 2,
    matching: 3, matched: 3,
    deciding: 4, decided: 4,
    validating: 5, validated: 5, validation_warning: 5,
    saving: 6, saved: 6,
  };
  return map[stageKey] ?? -1;
}

function isStageComplete(stageKey) {
  return [
    'loaded', 'jd_parsed', 'resume_parsed',
    'matched', 'decided', 'validated',
    'saved',
  ].includes(stageKey);
}


export default function Evaluations({ requisition, onBack }) {
  const [candidates, setCandidates] = useState([]);
  const [selectedCandidate, setSelectedCandidate] = useState(null);
  const [loading, setLoading] = useState(true);
  const [showUpload, setShowUpload] = useState(false);
  const [showOverride, setShowOverride] = useState(false);
  const [auditLog, setAuditLog] = useState([]);
  const [showAudit, setShowAudit] = useState(false);

  // SSE Evaluation state
  const [evalStream, setEvalStream] = useState(null); // EventSource ref
  const [evalProgress, setEvalProgress] = useState(null); // { candidateId, stages, currentStage, message, error }
  const [showFullTrace, setShowFullTrace] = useState(false);

  // Source documents panel (JD + resume raw text)
  const [showSourceDocs, setShowSourceDocs] = useState(false);
  const [sourceDocsTab, setSourceDocsTab] = useState('resume'); // 'resume' | 'jd'

  // Skill Assessment table filters & expanded evidence
  const [coverageSummaryExpanded, setCoverageSummaryExpanded] = useState(false);
  const [expandedSkills, setExpandedSkills] = useState(new Set());
  const [skillFilters, setSkillFilters] = useState({
    showNiceToHave: false,
    showUnmatchedOnly: false,
    priority: 'all',
    matchType: 'all',
    sort: 'priority',
  });

  // Upload form state
  const [uploadName, setUploadName] = useState('');
  const [uploadEmail, setUploadEmail] = useState('');
  const [uploadFile, setUploadFile] = useState(null);
  const [uploadResumeText, setUploadResumeText] = useState('');
  const [resumeInputMode, setResumeInputMode] = useState('file');
  const [uploading, setUploading] = useState(false);
  const fileRef = useRef(null);

  // Override form state
  const [overrideDecision, setOverrideDecision] = useState('hire');
  const [overrideReason, setOverrideReason] = useState('');

  useEffect(() => {
    loadCandidates();
    return () => {
      // Close any open SSE stream on unmount
      evalStream?.close();
    };
  }, [requisition.id]);

  async function loadCandidates() {
    setLoading(true);
    try {
      const data = await getCandidates(requisition.id);
      setCandidates(data || []);
    } catch (err) {
      console.error('Failed to load candidates:', err);
    } finally {
      setLoading(false);
    }
  }

  async function handleSelectCandidate(candidate) {
    try {
      const detail = await getCandidate(requisition.id, candidate.id);
      setSelectedCandidate(detail);
    } catch (err) {
      console.error('Failed to load candidate:', err);
    }
  }

  const hasResumeInput = resumeInputMode === 'file' ? !!uploadFile : uploadResumeText.trim().length > 50;

  async function handleUpload(e) {
    e.preventDefault();
    if (!uploadName || !hasResumeInput) return;
    setUploading(true);
    try {
      const formData = new FormData();
      formData.append('name', uploadName);
      if (uploadEmail) formData.append('email', uploadEmail);

      if (resumeInputMode === 'file' && uploadFile) {
        formData.append('resume', uploadFile);
      } else if (resumeInputMode === 'paste' && uploadResumeText.trim()) {
        formData.append('resume_text', uploadResumeText.trim());
      }

      const newCandidate = await createCandidate(requisition.id, formData);
      setShowUpload(false);
      setUploadName('');
      setUploadEmail('');
      setUploadFile(null);
      setUploadResumeText('');
      await loadCandidates();

      // Auto-start SSE evaluation for the new candidate
      if (newCandidate?.id) {
        handleSelectCandidate(newCandidate);
        startEvaluation(newCandidate.id, false);
      }
    } catch (err) {
      alert(`Upload failed: ${err.message}`);
    } finally {
      setUploading(false);
    }
  }

  // ── SSE Evaluation ──────────────────────────────────────────────────

  function startEvaluation(candidateId, force = false) {
    // Close any existing stream
    evalStream?.close();

    // Initialize progress state
    const progress = {
      candidateId,
      currentStage: null,
      completedStages: new Set(),
      message: 'Connecting...',
      error: null,
      startTime: Date.now(),
      done: false,
    };
    setEvalProgress({ ...progress, completedStages: [] });

    const es = evaluateCandidateSSE(requisition.id, candidateId, {
      onStage: (data) => {
        const idx = getStageIndex(data.stage);
        if (isStageComplete(data.stage)) {
          progress.completedStages.add(idx);
        }
        progress.currentStage = data.stage;
        progress.message = data.message;
        // Surface key signal stats from matching stage
        if (data.stage === 'matched') {
          progress.matchStats = {
            strong: data.strong_count,
            missing: data.missing_count,
            critical_missing: data.critical_missing,
            gaps: data.gaps_count,
          };
        }
        if (data.stage === 'decided') {
          progress.decisionStats = {
            recommendation: data.recommendation,
            confidence: data.confidence,
            score: data.composite_score,
          };
        }
        setEvalProgress({
          ...progress,
          completedStages: [...progress.completedStages],
        });
      },

      onCached: async (data) => {
        progress.message = 'Using cached evaluation';
        progress.done = true;
        setEvalProgress({
          ...progress,
          completedStages: [0, 1, 2, 3, 4],
        });
        // Refresh candidate detail
        const detail = await getCandidate(requisition.id, candidateId);
        setSelectedCandidate(detail);
        await loadCandidates();
        setTimeout(() => setEvalProgress(null), 2000);
      },

      onResult: async (data) => {
        progress.completedStages = new Set([0, 1, 2, 3, 4]);
        progress.message = `Evaluation complete — ${formatRecommendation(data.evaluation?.recommendation)}`;
        progress.done = true;
        setEvalProgress({
          ...progress,
          completedStages: [0, 1, 2, 3, 4],
        });
        // Refresh candidate detail to show evaluation
        const detail = await getCandidate(requisition.id, candidateId);
        setSelectedCandidate(detail);
        await loadCandidates();
        // Keep progress visible for a moment then clear
        setTimeout(() => setEvalProgress(null), 3000);
      },

      onError: (data) => {
        progress.error = data.message;
        progress.message = `Error: ${data.message}`;
        setEvalProgress({
          ...progress,
          completedStages: [...progress.completedStages],
        });
        // Clear after showing error
        setTimeout(() => setEvalProgress(null), 5000);
      },

      onDone: (data) => {
        // Stream ended
        setEvalStream(null);
      },
    }, force);

    setEvalStream(es);
  }

  async function handleOverride(e) {
    e.preventDefault();
    if (!overrideReason || overrideReason.length < 10) return;
    try {
      await overrideEvaluation(requisition.id, selectedCandidate.id, {
        decision: overrideDecision,
        reason: overrideReason,
        overridden_by: 'recruiter',
      });
      const detail = await getCandidate(requisition.id, selectedCandidate.id);
      setSelectedCandidate(detail);
      setShowOverride(false);
      setOverrideReason('');
    } catch (err) {
      alert(`Override failed: ${err.message}`);
    }
  }

  async function handleShowAudit() {
    try {
      const logs = await getAuditLog(requisition.id, selectedCandidate.id);
      setAuditLog(logs);
      setShowAudit(true);
    } catch (err) {
      console.error('Failed to load audit log:', err);
    }
  }

  async function handleDeleteCandidate(id, e) {
    e.stopPropagation();
    if (!confirm('Delete this candidate?')) return;
    try {
      await deleteCandidate(requisition.id, id);
      if (selectedCandidate?.id === id) setSelectedCandidate(null);
      await loadCandidates();
    } catch (err) {
      alert(`Delete failed: ${err.message}`);
    }
  }

  const evaluation = selectedCandidate?.evaluation;
  const isEvaluating = evalProgress && evalProgress.candidateId === selectedCandidate?.id && !evalProgress.done;

  return (
    <div>
      {/* Header */}
      <div className="page-header">
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-sm)', marginBottom: 4 }}>
            <button className="btn btn-secondary btn-sm" onClick={onBack}>
              ← Back
            </button>
            <span className="badge badge-info">{requisition.id}</span>
          </div>
          <h1 className="page-title">{requisition.title}</h1>
          <p className="page-subtitle">
            {requisition.department && `${requisition.department} · `}
            {requisition.location && `${requisition.location} · `}
            {requisition.employment_type}
          </p>
        </div>
        <button className="btn btn-primary" onClick={() => setShowUpload(true)}>
          📄 Add Candidate
        </button>
      </div>

      {/* Two-column layout */}
      <div style={{ display: 'grid', gridTemplateColumns: '340px 1fr', gap: 'var(--space-lg)', alignItems: 'start' }}>
        {/* Candidate List */}
        <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
          <div style={{ padding: 'var(--space-md) var(--space-lg)', borderBottom: '1px solid var(--border-subtle)' }}>
            <div className="card-title" style={{ fontSize: '0.9rem' }}>
              Candidates ({candidates.length})
            </div>
          </div>
          {loading ? (
            <div className="loading-overlay" style={{ padding: 'var(--space-xl)' }}>
              <div className="loading-spinner" />
            </div>
          ) : candidates.length === 0 ? (
            <div className="empty-state" style={{ padding: 'var(--space-xl)' }}>
              <div className="empty-state-icon">📄</div>
              <div style={{ fontSize: '0.85rem' }}>No candidates yet</div>
            </div>
          ) : (
            <div style={{ maxHeight: '70vh', overflowY: 'auto' }}>
              {candidates.map((c) => (
                <div
                  key={c.id}
                  onClick={() => handleSelectCandidate(c)}
                  style={{
                    padding: 'var(--space-md) var(--space-lg)',
                    borderBottom: '1px solid var(--border-subtle)',
                    cursor: 'pointer',
                    background: selectedCandidate?.id === c.id ? 'rgba(99,102,241,0.08)' : 'transparent',
                    transition: 'background 150ms',
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                  }}
                  onMouseEnter={(e) => { if (selectedCandidate?.id !== c.id) e.currentTarget.style.background = 'var(--bg-glass)'; }}
                  onMouseLeave={(e) => { if (selectedCandidate?.id !== c.id) e.currentTarget.style.background = 'transparent'; }}
                >
                  <div>
                    <div style={{ fontWeight: 600, fontSize: '0.9rem', color: 'var(--text-primary)' }}>
                      {c.name}
                    </div>
                    <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: 2 }}>
                      {c.email || c.id}
                    </div>
                    <div style={{ marginTop: 4 }}>
                      <span className={`badge ${getStatusBadgeClass(c.status)}`}>{c.status}</span>
                    </div>
                  </div>
                  <button
                    className="btn btn-danger btn-sm btn-icon"
                    onClick={(e) => handleDeleteCandidate(c.id, e)}
                    title="Delete"
                  >
                    🗑
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Candidate Detail / Evaluation */}
        <div>
          {!selectedCandidate ? (
            <div className="card">
              <div className="empty-state">
                <div className="empty-state-icon">👈</div>
                <div className="empty-state-text">Select a candidate to view details</div>
              </div>
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-lg)' }}>
              {/* Candidate Info Card */}
              <div className="card">
                <div className="card-header">
                  <div>
                    <div className="card-title">{selectedCandidate.name}</div>
                    <div className="card-subtitle">{selectedCandidate.email} {selectedCandidate.phone && `· ${selectedCandidate.phone}`}</div>
                  </div>
                  <div style={{ display: 'flex', gap: 'var(--space-sm)' }}>
                    {selectedCandidate.resume_filename && (
                      <span className="badge badge-info">📄 {selectedCandidate.resume_filename}</span>
                    )}
                    <button
                      className="btn btn-primary btn-sm"
                      onClick={() => startEvaluation(selectedCandidate.id, !!evaluation)}
                      disabled={isEvaluating}
                    >
                      {isEvaluating ? (
                        <>
                          <span className="loading-spinner" />
                          Evaluating...
                        </>
                      ) : evaluation ? (
                        '🔄 Re-evaluate'
                      ) : (
                        '🧠 Evaluate'
                      )}
                    </button>
                  </div>
                </div>
              </div>

              {/* SSE Pipeline Progress */}
              {evalProgress && evalProgress.candidateId === selectedCandidate.id && (
                <div className="card" style={{ borderColor: evalProgress.error ? 'var(--danger)' : 'var(--primary)', transition: 'border-color 300ms' }}>
                  <div className="card-title" style={{ marginBottom: 'var(--space-md)', display: 'flex', alignItems: 'center', gap: 'var(--space-sm)' }}>
                    {evalProgress.done && !evalProgress.error ? '✅' : evalProgress.error ? '❌' : <span className="loading-spinner" />}
                    <span>Evaluation Pipeline</span>
                    {evalProgress.startTime && !evalProgress.done && (
                      <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginLeft: 'auto' }}>
                        <ElapsedTimer start={evalProgress.startTime} />
                      </span>
                    )}
                  </div>

                  {/* Stage steps */}
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-xs)' }}>
                    {PIPELINE_STAGES.map((stage, i) => {
                      const completed = evalProgress.completedStages?.includes(i);
                      const active = getStageIndex(evalProgress.currentStage) === i && !completed;
                      const pending = !completed && !active;

                      return (
                        <div
                          key={stage.key}
                          style={{
                            display: 'flex',
                            alignItems: 'center',
                            gap: 'var(--space-sm)',
                            padding: 'var(--space-xs) var(--space-sm)',
                            borderRadius: 'var(--radius-sm)',
                            background: active ? 'rgba(99,102,241,0.08)' : 'transparent',
                            transition: 'all 300ms ease',
                            opacity: pending ? 0.4 : 1,
                          }}
                        >
                          <div style={{ width: 24, textAlign: 'center', fontSize: '0.9rem' }}>
                            {completed ? '✅' : active ? <span className="loading-spinner" style={{ width: 16, height: 16 }} /> : '⬜'}
                          </div>
                          <div style={{ fontSize: '0.85rem', fontWeight: active ? 600 : 400, color: completed ? 'var(--success)' : active ? 'var(--text-primary)' : 'var(--text-muted)' }}>
                            {stage.icon} {stage.label}
                          </div>
                          {active && (
                            <div style={{ marginLeft: 'auto', fontSize: '0.7rem', color: 'var(--text-muted)', fontStyle: 'italic', animation: 'fadeIn 300ms ease' }}>
                              {evalProgress.message}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>

                  {/* Live signal stats (while evaluating) */}
                  {!evalProgress.done && (evalProgress.matchStats || evalProgress.decisionStats) && (
                    <div style={{ marginTop: 'var(--space-sm)', display: 'flex', gap: 'var(--space-lg)', fontSize: '0.75rem', color: 'var(--text-muted)', flexWrap: 'wrap' }}>
                      {evalProgress.matchStats && (
                        <>
                          <span>Strong: <strong style={{ color: 'var(--success)' }}>{evalProgress.matchStats.strong}</strong></span>
                          <span>Missing: <strong style={{ color: evalProgress.matchStats.critical_missing > 0 ? 'var(--danger)' : 'var(--text-secondary)' }}>{evalProgress.matchStats.missing}</strong></span>
                          {evalProgress.matchStats.critical_missing > 0 && (
                            <span style={{ color: 'var(--danger)' }}>⚠ {evalProgress.matchStats.critical_missing} critical missing</span>
                          )}
                          <span>Gaps: {evalProgress.matchStats.gaps}</span>
                        </>
                      )}
                      {evalProgress.decisionStats && (() => {
                        const conf = evalProgress.decisionStats.confidence || 0;
                        const confLabel = conf >= 0.80 ? 'high' : conf >= 0.60 ? 'moderate' : conf >= 0.40 ? 'low' : 'very low';
                        return <span>→ {formatRecommendation(evalProgress.decisionStats.recommendation)} · {(conf * 100).toFixed(0)}% confidence ({confLabel})</span>;
                      })()}
                    </div>
                  )}

                  {/* Error message */}
                  {evalProgress.error && (
                    <div style={{ marginTop: 'var(--space-md)', padding: 'var(--space-sm)', background: 'var(--danger-bg)', borderRadius: 'var(--radius-sm)', fontSize: '0.85rem', color: 'var(--danger)' }}>
                      ⚠️ {evalProgress.error}
                    </div>
                  )}

                  {/* Done message */}
                  {evalProgress.done && !evalProgress.error && (
                    <div style={{ marginTop: 'var(--space-md)', padding: 'var(--space-sm)', background: 'var(--success-bg)', borderRadius: 'var(--radius-sm)', fontSize: '0.85rem', color: 'var(--success)' }}>
                      ✅ {evalProgress.message}
                    </div>
                  )}
                </div>
              )}

              {/* Evaluation Results */}
              {evaluation && (
                <>
                  {/* Decision Summary */}
                  <div className="card" style={{ borderColor: getRecommendationColor(evaluation.recommendation) }}>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr auto', gap: 'var(--space-xl)', alignItems: 'center' }}>
                      <div>
                        <div className="stat-label">Recommendation</div>
                        <span className={`badge ${getRecommendationBadge(evaluation.recommendation)}`} style={{ fontSize: '0.85rem', padding: '0.4rem 1rem' }}>
                          {formatRecommendation(evaluation.recommendation)}
                        </span>
                        {evaluation.override_decision && (
                          <div style={{ marginTop: 8 }}>
                            <span className={`badge ${getRecommendationBadge(evaluation.override_decision)}`}>
                              ✋ Override: {formatRecommendation(evaluation.override_decision)}
                            </span>
                          </div>
                        )}
                      </div>
                      <div>
                        <div className="stat-label">Composite Score</div>
                        <div className="stat-value">{evaluation.composite_score?.toFixed(0) || '—'}</div>
                        <div className="score-bar" style={{ marginTop: 8 }}>
                          <div
                            className={`score-bar-fill ${getScoreLevel(evaluation.composite_score)}`}
                            style={{ width: `${evaluation.composite_score || 0}%` }}
                          />
                        </div>
                      </div>
                      <div>
                        <div className="stat-label">Confidence</div>
                        <div className="stat-value">{(evaluation.confidence * 100).toFixed(0)}%</div>
                        <div style={{ fontSize: '0.7rem', marginTop: 2, marginBottom: 4, color:
                          evaluation.confidence >= 0.80 ? 'var(--strong-hire)' :
                          evaluation.confidence >= 0.60 ? '#facc15' :
                          evaluation.confidence >= 0.40 ? '#fb923c' : 'var(--no-hire)'
                        }}>
                          {evaluation.confidence >= 0.80 ? '● High — strong signal' :
                           evaluation.confidence >= 0.60 ? '● Moderate — verify in interview' :
                           evaluation.confidence >= 0.40 ? '● Low — manual review advised' :
                                                           '● Very low — insufficient signal'}
                        </div>
                        <div className="score-bar" style={{ marginTop: 4 }}>
                          <div
                            className={`score-bar-fill ${getScoreLevel(evaluation.confidence * 100)}`}
                            style={{ width: `${evaluation.confidence * 100}%` }}
                          />
                        </div>
                      </div>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-sm)' }}>
                        <button className="btn btn-secondary btn-sm" onClick={() => setShowOverride(true)}>
                          ✋ Override
                        </button>
                        <button className="btn btn-secondary btn-sm" onClick={handleShowAudit}>
                          📜 Audit
                        </button>
                      </div>
                    </div>
                    {(evaluation.model_used || evaluation.trace_id) && (
                      <div style={{ marginTop: 'var(--space-md)', fontSize: '0.7rem', color: 'var(--text-muted)', display: 'flex', gap: 'var(--space-md)', flexWrap: 'wrap' }}>
                        {evaluation.model_used && <span>Engine: {evaluation.model_used}</span>}
                        {evaluation.processing_time_ms && <span>· {evaluation.processing_time_ms}ms</span>}
                        {evaluation.trace_id && (
                          <span title="Pipeline trace ID for debugging">
                            · trace: <code style={{ fontFamily: 'var(--font-mono)', fontSize: '0.7rem' }}>{evaluation.trace_id}</code>
                          </span>
                        )}
                      </div>
                    )}
                  </div>

                  {/* ── Decision Summary ─────────────────────────────────── */}
                  {(evaluation.decision_summary || evaluation.explanation) && (
                    <div className="card">
                      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-sm)', marginBottom: 'var(--space-md)' }}>
                        <span style={{ fontSize: '1rem' }}>📝</span>
                        <span className="card-title" style={{ margin: 0 }}>Decision Summary</span>
                      </div>
                      <p style={{ fontSize: '0.875rem', lineHeight: 1.7, color: 'var(--text-secondary)', margin: 0 }}>
                        {evaluation.decision_summary || evaluation.explanation}
                      </p>
                    </div>
                  )}

                  {/* ── Score Breakdown ──────────────────────────────────── */}
                  {(() => {
                    const breakdown = computeScoreBreakdown(evaluation);
                    if (!breakdown.length) return null;
                    const drivers = computeScoreDrivers(evaluation);
                    return (
                      <div className="card">
                        <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-sm)', marginBottom: 'var(--space-md)' }}>
                          <span style={{ fontSize: '1rem' }}>🎯</span>
                          <span className="card-title" style={{ margin: 0 }}>Score Breakdown</span>
                          <span
                            title="Signal-driven scoring across 4 dimensions. Benchmark = 60% baseline."
                            style={{ fontSize: '0.7rem', color: 'var(--text-muted)', cursor: 'help', border: '1px solid var(--border)', borderRadius: '50%', width: 16, height: 16, display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }}
                          >i</span>
                        </div>

                        <div style={{ display: 'flex', gap: 'var(--space-lg)', alignItems: 'flex-start' }}>

                          {/* Left: dimension bars */}
                          <div style={{ flex: '1 1 55%', display: 'flex', flexDirection: 'column', gap: 10 }}>
                            {breakdown.map((cat) => {
                              const barColor =
                                cat.score >= 70 ? 'var(--success)' :
                                cat.score >= 40 ? '#eab308' : 'var(--danger)';
                              const vsSign  = cat.vsBaseline >= 0 ? '+' : '';
                              const vsColor = cat.vsBaseline >= 0 ? 'var(--success)' : 'var(--danger)';
                              const confColor =
                                cat.confidence === 'High'   ? 'var(--success)' :
                                cat.confidence === 'Medium' ? '#eab308' : 'var(--danger)';
                              const subSummary = cat.subScores
                                ? cat.subScores.map(ss => `${ss.label} ${ss.value}%`).join(' · ')
                                : null;
                              return (
                                <div key={cat.name}>
                                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                                      <span style={{ fontSize: '0.75rem', fontWeight: 700, letterSpacing: '0.06em', color: 'var(--text-primary)' }}>
                                        {cat.name}
                                      </span>
                                      <span style={{
                                        fontSize: '0.6rem', fontWeight: 700, padding: '1px 5px',
                                        borderRadius: 10, background: confColor + '22', color: confColor,
                                        border: `1px solid ${confColor}55`,
                                      }}>
                                        {cat.confidence}
                                      </span>
                                      <span style={{ fontSize: '0.62rem', color: 'var(--text-muted)' }}>
                                        {cat.percentile}
                                      </span>
                                    </div>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                                      <span style={{ fontSize: '0.68rem', color: vsColor }}>{vsSign}{cat.vsBaseline}%</span>
                                      <span style={{ fontSize: '0.88rem', fontWeight: 700, color: 'var(--text-primary)' }}>
                                        {cat.score}%
                                      </span>
                                    </div>
                                  </div>
                                  <div style={{ position: 'relative', height: 3, background: 'var(--border-subtle)', borderRadius: 2, overflow: 'hidden' }}>
                                    <div style={{ width: `${cat.score}%`, height: '100%', background: barColor, borderRadius: 2, transition: 'width 0.6s ease' }} />
                                    <div style={{ position: 'absolute', top: 0, bottom: 0, left: `${cat.benchmark}%`, width: 1, background: 'var(--text-muted)', opacity: 0.4 }} />
                                  </div>
                                  {subSummary && (
                                    <div style={{ marginTop: 3, fontSize: '0.65rem', color: 'var(--text-muted)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                                      {subSummary}
                                    </div>
                                  )}
                                </div>
                              );
                            })}
                          </div>

                          {/* Divider */}
                          {(drivers.positive.length > 0 || drivers.negative.length > 0) && (
                            <div style={{ width: 1, alignSelf: 'stretch', background: 'var(--border)', flexShrink: 0 }} />
                          )}

                          {/* Right: Score Drivers */}
                          {(drivers.positive.length > 0 || drivers.negative.length > 0) && (
                            <div style={{ flex: '1 1 40%', display: 'flex', flexDirection: 'column', gap: 'var(--space-md)' }}>
                              <div style={{ fontSize: '0.72rem', fontWeight: 700, color: 'var(--text-muted)', letterSpacing: '0.06em', textTransform: 'uppercase' }}>
                                Score Drivers
                              </div>
                              {drivers.positive.length > 0 && (
                                <div>
                                  <div style={{ fontSize: '0.7rem', fontWeight: 700, color: 'var(--success)', marginBottom: 5 }}>↑ Top Drivers</div>
                                  <ul style={{ margin: 0, padding: '0 0 0 14px', display: 'flex', flexDirection: 'column', gap: 4 }}>
                                    {drivers.positive.map((d, i) => (
                                      <li key={i} style={{ fontSize: '0.72rem', color: 'var(--text-secondary)', lineHeight: 1.4 }}>{d}</li>
                                    ))}
                                  </ul>
                                </div>
                              )}
                              {drivers.negative.length > 0 && (
                                <div>
                                  <div style={{ fontSize: '0.7rem', fontWeight: 700, color: 'var(--danger)', marginBottom: 5 }}>↓ Deductions</div>
                                  <ul style={{ margin: 0, padding: '0 0 0 14px', display: 'flex', flexDirection: 'column', gap: 4 }}>
                                    {drivers.negative.map((d, i) => (
                                      <li key={i} style={{ fontSize: '0.72rem', color: 'var(--text-secondary)', lineHeight: 1.4 }}>{d}</li>
                                    ))}
                                  </ul>
                                </div>
                              )}
                            </div>
                          )}

                        </div>
                      </div>
                    );
                  })()}


                  {/* ── Skill Assessment (pills + expandable detail table) ── */}
                  {evaluation.skill_matches && evaluation.skill_matches.length > 0 && (() => {
                    const MATCH_MAP = { strong: 'EXACT', weak: 'SEMANTIC', partial: 'PARTIAL', missing: 'MISSING' };
                    const PRIORITY_MAP = { critical: 'CRITICAL', important: 'IMPORTANT', secondary: 'NICE-TO-HAVE' };
                    const PRIORITY_ORDER = { critical: 0, important: 1, secondary: 2 };
                    const MATCH_STYLE = {
                      EXACT:    { bg: 'rgba(34,197,94,0.12)',  color: '#22c55e', border: '#22c55e' },
                      SEMANTIC: { bg: 'rgba(234,179,8,0.12)',  color: '#eab308', border: '#eab308' },
                      PARTIAL:  { bg: 'rgba(249,115,22,0.12)', color: '#f97316', border: '#f97316' },
                      MISSING:  { bg: 'rgba(239,68,68,0.12)',  color: '#ef4444', border: '#ef4444' },
                    };
                    const PRIORITY_STYLE = {
                      CRITICAL:     { bg: 'rgba(239,68,68,0.1)',   color: '#ef4444' },
                      IMPORTANT:    { bg: 'rgba(249,115,22,0.1)',  color: '#f97316' },
                      'NICE-TO-HAVE': { bg: 'rgba(148,163,184,0.1)', color: '#94a3b8' },
                    };

                    // Counts
                    const counts = { EXACT: 0, SEMANTIC: 0, PARTIAL: 0, MISSING: 0 };
                    evaluation.skill_matches.forEach(sm => { counts[MATCH_MAP[sm.match_level] || 'MISSING']++; });

                    // Filter
                    let filtered = evaluation.skill_matches.filter(sm => {
                      const matchLabel = MATCH_MAP[sm.match_level] || 'MISSING';
                      const priorityLabel = PRIORITY_MAP[sm.importance] || 'NICE-TO-HAVE';
                      if (!skillFilters.showNiceToHave && priorityLabel === 'NICE-TO-HAVE') return false;
                      if (skillFilters.showUnmatchedOnly && matchLabel !== 'MISSING' && matchLabel !== 'PARTIAL') return false;
                      if (skillFilters.priority !== 'all' && sm.importance !== skillFilters.priority) return false;
                      if (skillFilters.matchType !== 'all' && matchLabel !== skillFilters.matchType) return false;
                      return true;
                    });

                    // Sort
                    if (skillFilters.sort === 'priority') {
                      filtered = [...filtered].sort((a, b) => (PRIORITY_ORDER[a.importance] ?? 9) - (PRIORITY_ORDER[b.importance] ?? 9));
                    } else if (skillFilters.sort === 'match') {
                      const mOrder = { missing: 0, partial: 1, weak: 2, strong: 3 };
                      filtered = [...filtered].sort((a, b) => (mOrder[a.match_level] ?? 9) - (mOrder[b.match_level] ?? 9));
                    }

                    return (
                      <div className="card">
                        {/* Card header */}
                        <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-sm)', marginBottom: 'var(--space-md)' }}>
                          <span style={{ fontSize: '1rem' }}>🎯</span>
                          <span className="card-title" style={{ margin: 0 }}>Skill Assessment</span>
                        </div>

                        {/* Pill grid */}
                        <div className="skill-grid">
                          {evaluation.skill_matches.map((sm, i) => (
                            <div
                              key={i}
                              className={`skill-pill ${sm.match_level}`}
                              title={sm.evidence || 'No evidence provided'}
                            >
                              {sm.importance === 'critical' && '⚠️ '}
                              {sm.skill}
                              <span style={{ opacity: 0.7, marginLeft: 2 }}>
                                {sm.match_level === 'strong' ? '✓' : sm.match_level === 'partial' ? '◐' : sm.match_level === 'weak' ? '○' : '✗'}
                              </span>
                            </div>
                          ))}
                        </div>

                        {/* Legend row + expand toggle */}
                        <div style={{ marginTop: 'var(--space-md)', display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 'var(--space-sm)' }}>
                          <div style={{ display: 'flex', gap: 'var(--space-lg)', fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                            <span>✓ Strong</span>
                            <span>◐ Partial</span>
                            <span>○ Weak</span>
                            <span>✗ Missing</span>
                          </div>
                          <button
                            onClick={() => setCoverageSummaryExpanded(v => !v)}
                            style={{
                              display: 'flex', alignItems: 'center', gap: 6,
                              background: 'var(--bg-secondary)', border: '1px solid var(--border)',
                              borderRadius: 6, padding: '4px 10px', cursor: 'pointer',
                            }}
                          >
                            <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>
                              {coverageSummaryExpanded ? '▲' : '▼'}
                            </span>
                            <span style={{ fontSize: '0.72rem', fontWeight: 600, color: 'var(--text-secondary)', letterSpacing: '0.04em' }}>
                              {coverageSummaryExpanded ? 'Hide detail' : 'Coverage Summary & Detail'}
                            </span>
                            <div style={{ display: 'flex', gap: 4 }}>
                              {Object.entries(counts).map(([type, n]) => n > 0 && (
                                <span key={type} style={{
                                  fontSize: '0.65rem', fontWeight: 700, padding: '1px 6px',
                                  borderRadius: 4, border: `1px solid ${MATCH_STYLE[type].border}`,
                                  color: MATCH_STYLE[type].color, background: MATCH_STYLE[type].bg,
                                }}>
                                  {type[0]}{n}
                                </span>
                              ))}
                            </div>
                          </button>
                        </div>

                        {/* ── Expandable section: coverage bars + filters + table ── */}
                        {coverageSummaryExpanded && (
                          <div style={{ marginTop: 'var(--space-lg)', borderTop: '1px solid var(--border-subtle)', paddingTop: 'var(--space-lg)' }}>

                            {/* Coverage bars */}
                            <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 'var(--space-lg)' }}>
                              {Object.entries(counts).map(([type, n]) => (
                                <div key={type} style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-sm)' }}>
                                  <span style={{ width: 70, fontSize: '0.72rem', fontWeight: 600, color: MATCH_STYLE[type].color, letterSpacing: '0.04em' }}>{type}</span>
                                  <div style={{ flex: 1, height: 8, background: 'var(--border-subtle)', borderRadius: 4, overflow: 'hidden' }}>
                                    <div style={{
                                      width: `${(n / evaluation.skill_matches.length) * 100}%`,
                                      height: '100%', borderRadius: 4,
                                      background: MATCH_STYLE[type].color,
                                      transition: 'width 0.5s ease',
                                    }} />
                                  </div>
                                  <span style={{ width: 32, fontSize: '0.72rem', color: 'var(--text-muted)', textAlign: 'right' }}>
                                    {n}/{evaluation.skill_matches.length}
                                  </span>
                                </div>
                              ))}
                            </div>
                          </div>
                        )}

                        {/* Filter controls + table — shown when expanded */}
                        {coverageSummaryExpanded && (
                        <div>
                        {/* Filter controls */}
                        <div style={{ display: 'flex', gap: 'var(--space-sm)', flexWrap: 'wrap', alignItems: 'center', marginBottom: 'var(--space-md)', paddingBottom: 'var(--space-md)', borderBottom: '1px solid var(--border-subtle)' }}>
                          <label style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: '0.78rem', color: 'var(--text-secondary)', cursor: 'pointer', userSelect: 'none' }}>
                            <input type="checkbox" checked={skillFilters.showNiceToHave}
                              onChange={e => setSkillFilters(f => ({ ...f, showNiceToHave: e.target.checked }))} />
                            Show nice-to-have
                          </label>
                          <label style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: '0.78rem', color: 'var(--text-secondary)', cursor: 'pointer', userSelect: 'none' }}>
                            <input type="checkbox" checked={skillFilters.showUnmatchedOnly}
                              onChange={e => setSkillFilters(f => ({ ...f, showUnmatchedOnly: e.target.checked }))} />
                            Show unmatched only
                          </label>
                          <select
                            value={skillFilters.priority}
                            onChange={e => setSkillFilters(f => ({ ...f, priority: e.target.value }))}
                            style={{ fontSize: '0.78rem', padding: '3px 8px', borderRadius: 4, border: '1px solid var(--border)', background: 'var(--bg-input)', color: 'var(--text-secondary)', cursor: 'pointer' }}
                          >
                            <option value="all">All priorities</option>
                            <option value="critical">Critical</option>
                            <option value="important">Important</option>
                            <option value="secondary">Nice-to-have</option>
                          </select>
                          <select
                            value={skillFilters.matchType}
                            onChange={e => setSkillFilters(f => ({ ...f, matchType: e.target.value }))}
                            style={{ fontSize: '0.78rem', padding: '3px 8px', borderRadius: 4, border: '1px solid var(--border)', background: 'var(--bg-input)', color: 'var(--text-secondary)', cursor: 'pointer' }}
                          >
                            <option value="all">All match types</option>
                            <option value="EXACT">Exact</option>
                            <option value="SEMANTIC">Semantic</option>
                            <option value="PARTIAL">Partial</option>
                            <option value="MISSING">Missing</option>
                          </select>
                          <select
                            value={skillFilters.sort}
                            onChange={e => setSkillFilters(f => ({ ...f, sort: e.target.value }))}
                            style={{ fontSize: '0.78rem', padding: '3px 8px', borderRadius: 4, border: '1px solid var(--border)', background: 'var(--bg-input)', color: 'var(--text-secondary)', cursor: 'pointer' }}
                          >
                            <option value="priority">Sort: Priority</option>
                            <option value="match">Sort: Match quality</option>
                          </select>
                        </div>

                        {/* Table */}
                        <div style={{ overflowX: 'auto' }}>
                          {/* Table header */}
                          <div style={{ display: 'grid', gridTemplateColumns: '1fr 110px 110px 160px', gap: 'var(--space-sm)', padding: '6px var(--space-sm)', borderBottom: '1px solid var(--border-subtle)', marginBottom: 4 }}>
                            {['REQUIREMENT', 'PRIORITY', 'MATCH', 'EVIDENCE'].map(h => (
                              <span key={h} style={{ fontSize: '0.68rem', fontWeight: 700, letterSpacing: '0.08em', color: 'var(--text-muted)', textTransform: 'uppercase' }}>{h}</span>
                            ))}
                          </div>

                          {/* Rows */}
                          {filtered.length === 0 ? (
                            <div style={{ padding: 'var(--space-md)', fontSize: '0.82rem', color: 'var(--text-muted)', textAlign: 'center' }}>
                              No skills match current filters
                            </div>
                          ) : filtered.map((sm, i) => {
                            const matchLabel = MATCH_MAP[sm.match_level] || 'MISSING';
                            const priorityLabel = PRIORITY_MAP[sm.importance] || 'NICE-TO-HAVE';
                            const ms = MATCH_STYLE[matchLabel];
                            const ps = PRIORITY_STYLE[priorityLabel];
                            const isMissing = matchLabel === 'MISSING';
                            const isExpanded = expandedSkills.has(i);
                            const hasEvidence = sm.evidence && sm.evidence.trim().length > 0;
                            const hasReason = sm.match_reason && sm.match_reason.trim().length > 0;

                            return (
                              <div key={i} style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                                <div style={{
                                  display: 'grid', gridTemplateColumns: '1fr 110px 110px 160px',
                                  gap: 'var(--space-sm)', padding: '8px var(--space-sm)',
                                  alignItems: 'center',
                                  background: isExpanded ? 'var(--bg-primary)' : 'transparent',
                                  transition: 'background 200ms',
                                }}>
                                  {/* Requirement */}
                                  <span style={{ fontSize: '0.84rem', color: 'var(--text-primary)', fontWeight: sm.importance === 'critical' ? 600 : 400 }}>
                                    {sm.skill}
                                  </span>
                                  {/* Priority badge */}
                                  <span style={{
                                    fontSize: '0.68rem', fontWeight: 700, padding: '2px 7px',
                                    borderRadius: 4, background: ps.bg, color: ps.color,
                                    display: 'inline-block', textAlign: 'center', letterSpacing: '0.04em',
                                  }}>
                                    {priorityLabel}
                                  </span>
                                  {/* Match badge */}
                                  <span style={{
                                    fontSize: '0.68rem', fontWeight: 700, padding: '2px 7px',
                                    borderRadius: 4, background: ms.bg, color: ms.color,
                                    border: `1px solid ${ms.border}`, display: 'inline-block',
                                    textAlign: 'center', letterSpacing: '0.04em',
                                  }}>
                                    {matchLabel}
                                  </span>
                                  {/* Evidence link */}
                                  <button
                                    onClick={() => setExpandedSkills(prev => {
                                      const next = new Set(prev);
                                      next.has(i) ? next.delete(i) : next.add(i);
                                      return next;
                                    })}
                                    style={{
                                      background: 'none', border: 'none', cursor: 'pointer', padding: 0,
                                      fontSize: '0.78rem', color: 'var(--primary-400)',
                                      textAlign: 'left', display: 'flex', alignItems: 'center', gap: 4,
                                    }}
                                  >
                                    <span style={{ fontSize: '0.72rem' }}>{isExpanded ? '▲' : '▼'}</span>
                                    {isMissing ? 'Why missing' : 'View proof'}
                                  </button>
                                </div>

                                {/* Expanded evidence panel */}
                                {isExpanded && (
                                  <div style={{
                                    padding: 'var(--space-sm) var(--space-md)',
                                    background: 'var(--bg-primary)',
                                    borderTop: `1px solid ${ms.border}`,
                                    borderLeft: `3px solid ${ms.color}`,
                                    marginBottom: 2,
                                  }}>
                                    {isMissing ? (
                                      <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)', fontStyle: 'italic' }}>
                                        {hasReason ? sm.match_reason : `No mention of ${sm.skill} found in resume.`}
                                      </div>
                                    ) : (
                                      <>
                                        {hasEvidence && (
                                          <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', lineHeight: 1.55 }}>
                                            <span style={{ fontWeight: 600, color: 'var(--text-muted)', fontSize: '0.7rem', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Evidence: </span>
                                            {sm.evidence}
                                          </div>
                                        )}
                                        {hasReason && (
                                          <div style={{ marginTop: hasEvidence ? 4 : 0, fontSize: '0.75rem', color: 'var(--text-muted)', fontStyle: 'italic' }}>
                                            {sm.match_reason}
                                          </div>
                                        )}
                                        {!hasEvidence && !hasReason && (
                                          <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)', fontStyle: 'italic' }}>No evidence details recorded.</div>
                                        )}
                                      </>
                                    )}
                                  </div>
                                )}
                              </div>
                            );
                          })}
                        </div>
                        </div>
                        )}
                      </div>
                    );
                  })()}

                  {/* Strengths & Gaps — signal-derived with evidence */}
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 'var(--space-lg)' }}>
                    {evaluation.strengths && evaluation.strengths.length > 0 && (
                      <div className="card">
                        <div className="card-title" style={{ marginBottom: 'var(--space-md)', color: 'var(--success)' }}>
                          💪 Strengths
                        </div>
                        <ul style={{ listStyle: 'none', display: 'flex', flexDirection: 'column', gap: 'var(--space-sm)' }}>
                          {evaluation.strengths.map((s, i) => {
                            // Support both legacy string and new {description, evidence, skill} object
                            const desc = typeof s === 'string' ? s : s.description;
                            const evidence = typeof s === 'object' ? s.evidence : null;
                            return (
                              <li key={i} style={{ fontSize: '0.85rem', padding: 'var(--space-sm)', background: 'var(--success-bg)', borderRadius: 'var(--radius-sm)', color: 'var(--text-secondary)' }}>
                                <div>✅ {desc}</div>
                                {evidence && (
                                  <div style={{ marginTop: 4, fontSize: '0.75rem', color: 'var(--text-muted)', fontStyle: 'italic', paddingLeft: 'var(--space-sm)', borderLeft: '2px solid var(--success)' }}>
                                    {evidence}
                                  </div>
                                )}
                              </li>
                            );
                          })}
                        </ul>
                      </div>
                    )}
                    {evaluation.gaps && evaluation.gaps.length > 0 && (() => {
                        const gapGroups = [
                          {
                            key: 'critical',
                            label: 'Critical Gaps',
                            icon: '🔴',
                            color: '#ef4444',
                            bg: 'rgba(239,68,68,0.08)',
                            border: 'rgba(239,68,68,0.3)',
                            match: g => (typeof g === 'object' ? g.severity : null) === 'critical',
                          },
                          {
                            key: 'important',
                            label: 'Weak Evidence',
                            icon: '🟠',
                            color: '#f97316',
                            bg: 'rgba(249,115,22,0.08)',
                            border: 'rgba(249,115,22,0.3)',
                            match: g => (typeof g === 'object' ? g.severity : null) === 'important',
                          },
                          {
                            key: 'minor',
                            label: 'Nice-to-have Missing',
                            icon: '🔵',
                            color: '#94a3b8',
                            bg: 'rgba(148,163,184,0.08)',
                            border: 'rgba(148,163,184,0.25)',
                            match: g => {
                              const sev = typeof g === 'object' ? g.severity : null;
                              return sev === 'minor' || sev == null;
                            },
                          },
                        ];
                        const filledGroups = gapGroups
                          .map(grp => ({ ...grp, items: evaluation.gaps.filter(grp.match) }))
                          .filter(grp => grp.items.length > 0);
                        if (!filledGroups.length) return null;
                        return (
                          <div className="card">
                            <div className="card-title" style={{ marginBottom: 'var(--space-md)', color: 'var(--danger)' }}>
                              ⚠️ Gaps & Risks
                            </div>
                            <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-md)' }}>
                              {filledGroups.map(grp => (
                                <div key={grp.key} style={{ padding: '10px 12px', borderRadius: 8, background: grp.bg, border: `1px solid ${grp.border}` }}>
                                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8 }}>
                                    <span style={{ fontSize: '0.7rem' }}>{grp.icon}</span>
                                    <span style={{ fontSize: '0.72rem', fontWeight: 700, letterSpacing: '0.06em', color: grp.color, textTransform: 'uppercase' }}>
                                      {grp.label}
                                    </span>
                                    <span style={{ fontSize: '0.68rem', color: grp.color, opacity: 0.7 }}>
                                      ({grp.items.length})
                                    </span>
                                  </div>
                                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                                    {grp.items.map((g, i) => {
                                      const name = typeof g === 'object' ? (g.skill || g.description) : g;
                                      const impact = typeof g === 'object' ? g.impact : null;
                                      return (
                                        <span
                                          key={i}
                                          title={impact || name}
                                          style={{
                                            fontSize: '0.78rem', padding: '3px 10px', borderRadius: 20,
                                            background: 'var(--bg-primary)', color: 'var(--text-secondary)',
                                            border: `1px solid ${grp.border}`, cursor: impact ? 'help' : 'default',
                                          }}
                                        >
                                          {name}
                                        </span>
                                      );
                                    })}
                                  </div>
                                </div>
                              ))}
                            </div>
                          </div>
                        );
                    })()}
                  </div>

                  {/* Decision Trace — summary + full detail */}
                  {evaluation.decision_trace && evaluation.decision_trace.length > 0 && (() => {
                    const SUMMARY_SIGNALS = new Set(['skill_match', 'critical_gap_check', 'experience', 'recommendation']);
                    const summarySteps = evaluation.decision_trace.filter(s =>
                      SUMMARY_SIGNALS.has(s.signal || s.action || '') || s.impact !== 'neutral'
                    );
                    const displaySteps = showFullTrace ? evaluation.decision_trace : summarySteps;
                    const hiddenCount = evaluation.decision_trace.length - summarySteps.length;
                    return (
                      <div className="card">
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 'var(--space-lg)' }}>
                          <div className="card-title">🔍 Decision Reasoning</div>
                          <button
                            onClick={() => setShowFullTrace(f => !f)}
                            style={{ fontSize: '0.7rem', background: 'none', border: '1px solid var(--border)', borderRadius: 4, padding: '2px 8px', cursor: 'pointer', color: 'var(--text-muted)' }}
                          >
                            {showFullTrace ? 'Show summary' : `Full analysis (${evaluation.decision_trace.length} steps)`}
                          </button>
                        </div>
                        <div className="decision-trace">
                          {displaySteps.map((step, i) => (
                            <div key={step.step} className="trace-step" style={{ animationDelay: `${i * 0.08}s` }}>
                              <div className={`trace-dot ${step.impact || 'neutral'}`} />
                              <div className="trace-content">
                                <div className="trace-action">
                                  {step.signal || step.action}
                                  {showFullTrace && step.weight != null && (
                                    <span style={{ marginLeft: 8, fontSize: '0.7rem', color: 'var(--text-muted)', fontWeight: 400 }}>
                                      {(step.weight * 100).toFixed(0)}% weight
                                    </span>
                                  )}
                                </div>
                                <div className="trace-finding">{step.finding}</div>
                              </div>
                            </div>
                          ))}
                        </div>
                        {!showFullTrace && hiddenCount > 0 && (
                          <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)', marginTop: 'var(--space-sm)', paddingLeft: 20 }}>
                            {hiddenCount} supporting step(s) hidden — click Full analysis to expand
                          </div>
                        )}
                      </div>
                    );
                  })()}

                  {/* Suggested Actions */}
                  {evaluation.suggested_actions && evaluation.suggested_actions.length > 0 && (
                    <div className="card">
                      <div className="card-title" style={{ marginBottom: 'var(--space-md)' }}>📋 Suggested Next Steps</div>
                      <ul style={{ listStyle: 'none', display: 'flex', flexDirection: 'column', gap: 'var(--space-sm)' }}>
                        {evaluation.suggested_actions.map((action, i) => (
                          <li key={i} style={{ fontSize: '0.85rem', padding: 'var(--space-sm) var(--space-md)', background: 'var(--info-bg)', borderRadius: 'var(--radius-sm)', borderLeft: '3px solid var(--info)', color: 'var(--text-secondary)' }}>
                            {action}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </>
              )}

              {/* ── Source Documents ──────────────────────────────────────── */}
              {(selectedCandidate?.resume_text || requisition?.description_raw) && (
                <div className="card">
                  <div
                    className="card-header"
                    style={{ cursor: 'pointer', userSelect: 'none' }}
                    onClick={() => setShowSourceDocs(v => !v)}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-sm)' }}>
                      <span style={{ fontSize: '1rem' }}>📄</span>
                      <span className="card-title" style={{ margin: 0 }}>Source Documents</span>
                      <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginLeft: 4 }}>
                        {selectedCandidate?.resume_text ? `resume · ${selectedCandidate.resume_text.length.toLocaleString()} chars` : ''}
                        {selectedCandidate?.resume_text && requisition?.description_raw ? ' · ' : ''}
                        {requisition?.description_raw ? `JD · ${requisition.description_raw.length.toLocaleString()} chars` : ''}
                      </span>
                    </div>
                    <span style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>
                      {showSourceDocs ? '▲ Hide' : '▼ Show'}
                    </span>
                  </div>

                  {showSourceDocs && (
                    <div style={{ marginTop: 'var(--space-md)' }}>
                      {/* Tab selector */}
                      <div style={{ display: 'flex', gap: 'var(--space-xs)', marginBottom: 'var(--space-md)' }}>
                        {selectedCandidate?.resume_text && (
                          <button
                            className={`btn btn-sm ${sourceDocsTab === 'resume' ? 'btn-primary' : 'btn-secondary'}`}
                            onClick={() => setSourceDocsTab('resume')}
                          >
                            📄 Resume
                          </button>
                        )}
                        {requisition?.description_raw && (
                          <button
                            className={`btn btn-sm ${sourceDocsTab === 'jd' ? 'btn-primary' : 'btn-secondary'}`}
                            onClick={() => setSourceDocsTab('jd')}
                          >
                            📋 Job Description
                          </button>
                        )}
                      </div>

                      {/* Content */}
                      {sourceDocsTab === 'resume' && selectedCandidate?.resume_text && (
                        <div>
                          <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginBottom: 'var(--space-xs)', display: 'flex', justifyContent: 'space-between' }}>
                            <span>Resume text as extracted from uploaded file</span>
                            <span>{selectedCandidate.resume_text.length.toLocaleString()} characters</span>
                          </div>
                          <textarea
                            readOnly
                            value={selectedCandidate.resume_text}
                            style={{
                              width: '100%',
                              height: '320px',
                              fontFamily: 'var(--font-mono)',
                              fontSize: '0.75rem',
                              lineHeight: 1.55,
                              background: 'var(--bg-input)',
                              border: '1px solid var(--border)',
                              borderRadius: 'var(--radius-sm)',
                              color: 'var(--text-primary)',
                              padding: 'var(--space-md)',
                              resize: 'vertical',
                              boxSizing: 'border-box',
                            }}
                          />
                        </div>
                      )}

                      {sourceDocsTab === 'jd' && requisition?.description_raw && (
                        <div>
                          <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginBottom: 'var(--space-xs)', display: 'flex', justifyContent: 'space-between' }}>
                            <span>Job description as entered at requisition creation</span>
                            <span>{requisition.description_raw.length.toLocaleString()} characters</span>
                          </div>
                          <textarea
                            readOnly
                            value={requisition.description_raw}
                            style={{
                              width: '100%',
                              height: '320px',
                              fontFamily: 'var(--font-mono)',
                              fontSize: '0.75rem',
                              lineHeight: 1.55,
                              background: 'var(--bg-input)',
                              border: '1px solid var(--border)',
                              borderRadius: 'var(--radius-sm)',
                              color: 'var(--text-primary)',
                              padding: 'var(--space-md)',
                              resize: 'vertical',
                              boxSizing: 'border-box',
                            }}
                          />
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )}

            </div>
          )}
        </div>
      </div>

      {/* Upload Modal */}
      {showUpload && (
        <div className="modal-overlay" onClick={() => setShowUpload(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h2 className="modal-title">Add Candidate</h2>
              <button className="modal-close" onClick={() => setShowUpload(false)}>✕</button>
            </div>
            <form onSubmit={handleUpload}>
              <div className="modal-body">
                <div className="form-group">
                  <label className="form-label">Candidate Name *</label>
                  <input
                    className="form-input"
                    placeholder="Full name"
                    value={uploadName}
                    onChange={(e) => setUploadName(e.target.value)}
                    required
                  />
                </div>
                <div className="form-group">
                  <label className="form-label">Email</label>
                  <input
                    className="form-input"
                    type="email"
                    placeholder="candidate@email.com"
                    value={uploadEmail}
                    onChange={(e) => setUploadEmail(e.target.value)}
                  />
                </div>
                <div className="form-group">
                  <label className="form-label">Resume *</label>
                  <div style={{ display: 'flex', gap: 'var(--space-xs)', marginBottom: 'var(--space-sm)' }}>
                    <button
                      type="button"
                      className={`btn btn-sm ${resumeInputMode === 'file' ? 'btn-primary' : 'btn-secondary'}`}
                      onClick={() => setResumeInputMode('file')}
                    >
                      📁 Upload File
                    </button>
                    <button
                      type="button"
                      className={`btn btn-sm ${resumeInputMode === 'paste' ? 'btn-primary' : 'btn-secondary'}`}
                      onClick={() => setResumeInputMode('paste')}
                    >
                      📋 Paste Text
                    </button>
                  </div>

                  {resumeInputMode === 'file' ? (
                    <>
                      <div
                        className={`file-upload-zone ${uploadFile ? 'active' : ''}`}
                        onClick={() => fileRef.current?.click()}
                      >
                        <div className="file-upload-icon">📄</div>
                        {uploadFile ? (
                          <div className="file-upload-text">
                            <strong>{uploadFile.name}</strong>
                            <div style={{ fontSize: '0.75rem', marginTop: 4, color: 'var(--text-muted)' }}>
                              {(uploadFile.size / 1024).toFixed(0)} KB
                            </div>
                          </div>
                        ) : (
                          <div className="file-upload-text">
                            <strong>Click to upload</strong> or drag & drop
                            <div style={{ fontSize: '0.75rem', marginTop: 4, color: 'var(--text-muted)' }}>
                              PDF, DOCX, or TXT (max 10MB)
                            </div>
                          </div>
                        )}
                      </div>
                      <input
                        ref={fileRef}
                        type="file"
                        accept=".pdf,.docx,.doc,.txt"
                        style={{ display: 'none' }}
                        onChange={(e) => setUploadFile(e.target.files[0])}
                      />
                    </>
                  ) : (
                    <>
                      <textarea
                        className="form-textarea"
                        placeholder="Paste the full resume text here..."
                        value={uploadResumeText}
                        onChange={(e) => setUploadResumeText(e.target.value)}
                        rows={10}
                        style={{ fontFamily: 'var(--font-mono)', fontSize: '0.8rem' }}
                      />
                      <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: 4 }}>
                        {uploadResumeText.length} chars {uploadResumeText.length < 50 && uploadResumeText.length > 0 ? '(min 50)' : ''}
                      </div>
                    </>
                  )}
                </div>
                <div style={{ padding: 'var(--space-sm)', background: 'var(--info-bg)', borderRadius: 'var(--radius-sm)', fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
                  ⚡ AI evaluation will stream results in real-time after submission
                </div>
              </div>
              <div className="modal-footer">
                <button type="button" className="btn btn-secondary" onClick={() => setShowUpload(false)}>
                  Cancel
                </button>
                <button type="submit" className="btn btn-primary" disabled={uploading || !uploadName || !hasResumeInput}>
                  {uploading ? <><span className="loading-spinner" /> Adding...</> : '⚡ Add & Evaluate'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Override Modal */}
      {showOverride && (
        <div className="modal-overlay" onClick={() => setShowOverride(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h2 className="modal-title">Override Decision</h2>
              <button className="modal-close" onClick={() => setShowOverride(false)}>✕</button>
            </div>
            <form onSubmit={handleOverride}>
              <div className="modal-body">
                <p style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', marginBottom: 'var(--space-lg)' }}>
                  Current AI recommendation: <strong>{formatRecommendation(evaluation?.recommendation)}</strong>
                </p>
                <div className="form-group">
                  <label className="form-label">Your Decision</label>
                  <select
                    className="form-select"
                    value={overrideDecision}
                    onChange={(e) => setOverrideDecision(e.target.value)}
                  >
                    <option value="strong_hire">Strong Hire</option>
                    <option value="hire">Hire</option>
                    <option value="consider">Consider</option>
                    <option value="no_hire">No Hire</option>
                  </select>
                </div>
                <div className="form-group">
                  <label className="form-label">Reason (min 10 chars) *</label>
                  <textarea
                    className="form-textarea"
                    placeholder="Explain your reasoning for this override..."
                    value={overrideReason}
                    onChange={(e) => setOverrideReason(e.target.value)}
                    rows={4}
                    required
                  />
                </div>
              </div>
              <div className="modal-footer">
                <button type="button" className="btn btn-secondary" onClick={() => setShowOverride(false)}>
                  Cancel
                </button>
                <button type="submit" className="btn btn-primary" disabled={overrideReason.length < 10}>
                  Save Override
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Audit Log Modal */}
      {showAudit && (
        <div className="modal-overlay" onClick={() => setShowAudit(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()} style={{ maxWidth: '720px' }}>
            <div className="modal-header">
              <h2 className="modal-title">📜 Audit Trail</h2>
              <button className="modal-close" onClick={() => setShowAudit(false)}>✕</button>
            </div>
            <div className="modal-body">
              {auditLog.length === 0 ? (
                <div className="empty-state">
                  <div>No audit entries found</div>
                </div>
              ) : (
                <div className="decision-trace">
                  {auditLog.map((log) => (
                    <div key={log.id} className="trace-step">
                      <div className={`trace-dot ${log.action === 'override' ? 'negative' : 'positive'}`} />
                      <div className="trace-content">
                        <div className="trace-action">
                          {log.action.toUpperCase()} — by {log.actor}
                        </div>
                        <div className="trace-finding" style={{ fontSize: '0.8rem' }}>
                          {new Date(log.created_at).toLocaleString()}
                        </div>
                        {log.details && (
                          <pre style={{
                            marginTop: 8,
                            padding: 'var(--space-sm)',
                            background: 'var(--bg-primary)',
                            borderRadius: 'var(--radius-sm)',
                            fontSize: '0.75rem',
                            color: 'var(--text-muted)',
                            overflow: 'auto',
                            fontFamily: 'var(--font-mono)',
                          }}>
                            {JSON.stringify(log.details, null, 2)}
                          </pre>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}


// ── Elapsed Timer Component ─────────────────────────────────────────────

function ElapsedTimer({ start }) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const timer = setInterval(() => {
      setElapsed(((Date.now() - start) / 1000).toFixed(1));
    }, 100);
    return () => clearInterval(timer);
  }, [start]);
  return <>{elapsed}s</>;
}


// ── Helpers ──────────────────────────────────────────────────────────────

function getStatusBadgeClass(status) {
  switch (status) {
    case 'evaluated': return 'badge-success';
    case 'pending': return 'badge-warning';
    case 'flagged': return 'badge-danger';
    case 'hired': return 'badge-info';
    case 'rejected': return 'badge-danger';
    default: return 'badge-info';
  }
}

function getRecommendationBadge(rec) {
  switch (rec) {
    case 'strong_hire': return 'badge-strong-hire';
    case 'hire': return 'badge-hire';
    case 'consider': return 'badge-consider';
    case 'no_hire': return 'badge-no-hire';
    default: return 'badge-info';
  }
}

function getRecommendationColor(rec) {
  switch (rec) {
    case 'strong_hire': return 'rgba(16, 185, 129, 0.3)';
    case 'hire': return 'rgba(52, 211, 153, 0.3)';
    case 'consider': return 'rgba(245, 158, 11, 0.3)';
    case 'no_hire': return 'rgba(239, 68, 68, 0.3)';
    default: return 'var(--border-subtle)';
  }
}

function formatRecommendation(rec) {
  switch (rec) {
    case 'strong_hire': return 'Strong Hire';
    case 'hire': return 'Hire';
    case 'consider': return 'Consider';
    case 'no_hire': return 'No Hire';
    default: return rec || '—';
  }
}

function getScoreLevel(score) {
  if (score >= 70) return 'high';
  if (score >= 40) return 'medium';
  return 'low';
}

// ── Score Breakdown computation ─────────────────────────────────────────────
// Derives per-dimension scores from available evaluation signals.

// Keyword pools for Execution Capability signal detection
const KW_ARCHITECTURE = ['architect','design pattern','microservice','system design','distributed','scalab','api design','schema','infrastructure','technical lead','led design','designed the'];
const KW_OWNERSHIP    = ['owned','led','built','launched','delivered','drove','responsible for','managed the','end-to-end','from scratch','solo','founded'];
const KW_LEADERSHIP   = ['managed','mentored','coached','hired','grew the team','cross-functional','stakeholder','director','vp ','head of','team of','reports to'];
const KW_SCALE        = ['million user','billion','10x','scale','high traffic','high availability','99.','production','enterprise','global','petabyte','terabyte'];

function kwHitRate(textPool, keywords) {
  if (!textPool || !textPool.length) return 0;
  const blob = textPool.join(' ').toLowerCase();
  const hits = keywords.filter(kw => blob.includes(kw.toLowerCase())).length;
  return Math.min(1, hits / Math.max(keywords.length * 0.3, 1));
}

// Backend may return scores as 0–1 floats OR already-normalised 0–100 integers.
function normaliseScore(raw) {
  if (raw == null) return null;
  return Math.round(Math.min(100, raw > 1 ? raw : raw * 100));
}

// Percentile lookup: score → approximate talent-pool standing
function estimatePercentile(score) {
  if (score >= 90) return 'Top 5%';
  if (score >= 80) return 'Top 15%';
  if (score >= 70) return 'Top 30%';
  if (score >= 60) return 'Top 50%';
  if (score >= 45) return 'Top 65%';
  return 'Bottom 35%';
}

function computeScoreBreakdown(evaluation) {
  const BENCHMARK = 60;
  const categories = [];

  // ── TECHNICAL ──────────────────────────────────────────────────────────────
  // Formula: 0.50 * required_match + 0.15 * nice_to_have + 0.20 * depth + 0.15 * recency
  const skills = evaluation.skill_matches || [];
  if (skills.length > 0) {
    const required   = skills.filter(s => s.importance === 'critical' || s.importance === 'important');
    const niceToHave = skills.filter(s => s.importance === 'secondary' || s.importance === 'nice-to-have');

    const matchRate = (pool) => {
      if (!pool.length) return 0;
      return pool.filter(s => s.match_level === 'strong' || s.match_level === 'partial').length / pool.length;
    };

    const requiredMatch = required.length  ? matchRate(required)   : matchRate(skills);
    const nthMatch      = niceToHave.length ? matchRate(niceToHave) : requiredMatch * 0.8;

    // Depth: avg skill_score (0–1) of critical/important pool
    const depthPool = required.length ? required : skills;
    const depth     = depthPool.reduce((s, x) => s + (x.skill_score ?? 0), 0) / depthPool.length;

    // Recency proxy: fraction of skills with strong match
    const strongRatio = skills.filter(s => s.match_level === 'strong').length / skills.length;

    const techScore = Math.round(100 * (0.50 * requiredMatch + 0.15 * nthMatch + 0.20 * depth + 0.15 * strongRatio));
    const confidence = required.length >= 3 ? 'High' : required.length >= 1 ? 'Medium' : 'Low';

    categories.push({
      name: 'TECHNICAL',
      label: 'Technical Skills',
      score: Math.max(0, Math.min(100, techScore)),
      weight: 35,
      benchmark: BENCHMARK,
      confidence,
      subScores: [
        { label: 'Required skills',  value: Math.round(requiredMatch * 100) },
        { label: 'Nice-to-have',     value: Math.round(nthMatch * 100) },
        { label: 'Skill depth',      value: Math.round(depth * 100) },
        { label: 'Recency',          value: Math.round(strongRatio * 100) },
      ],
    });
  }

  // ── EXPERIENCE ─────────────────────────────────────────────────────────────
  const expAssess = evaluation.experience_assessment || {};
  const expScore  = normaliseScore(expAssess.score);
  if (expScore != null) {
    const yearsMatch = normaliseScore(expAssess.years_match_score ?? expAssess.score) ?? expScore;
    const domainRel  = normaliseScore(expAssess.domain_relevance   ?? expAssess.score) ?? expScore;
    const seniority  = normaliseScore(expAssess.seniority_signal   ?? expAssess.score) ?? expScore;
    const confidence = expScore >= 70 ? 'High' : expScore >= 45 ? 'Medium' : 'Low';
    categories.push({
      name: 'EXPERIENCE',
      label: 'Experience',
      score: expScore,
      weight: 25,
      benchmark: BENCHMARK,
      confidence,
      subScores: [
        { label: 'Years match',      value: yearsMatch },
        { label: 'Domain relevance', value: domainRel },
        { label: 'Seniority signal', value: seniority },
      ],
    });
  }

  // ── EXECUTION CAPABILITY ───────────────────────────────────────────────────
  // Reads structured scores from backend (pipeline_schemas.ExecutionCapabilityAssessment).
  // Backend scans real resume text (highlights, achievements, skill evidence) —
  // much richer signal than frontend keyword-scanning on LLM summary text.
  const execCap = evaluation.execution_capability;
  if (execCap && execCap.composite_score != null) {
    // Backend confidence is 'low'|'medium' — capitalise for display
    const execConf = execCap.confidence
      ? execCap.confidence.charAt(0).toUpperCase() + execCap.confidence.slice(1)
      : 'Low';
    categories.push({
      name: 'EXECUTION CAPABILITY',
      label: 'Execution Capability',
      score: Math.round(execCap.composite_score),
      weight: 25,
      benchmark: BENCHMARK,
      confidence: execConf,
      subScores: [
        { label: 'System design',     value: Math.round(execCap.system_design_score    ?? 0) },
        { label: 'Project ownership', value: Math.round(execCap.project_ownership_score ?? 0) },
        { label: 'Leadership',        value: Math.round(execCap.leadership_score        ?? 0) },
        { label: 'Production scale',  value: Math.round(execCap.production_scale_score  ?? 0) },
      ],
    });
  }

  // ── ACADEMIC ───────────────────────────────────────────────────────────────
  const acadAssess = evaluation.education_assessment || {};
  const acadScore  = normaliseScore(acadAssess.score);
  if (acadScore != null) {
    const degreeLevel = normaliseScore(acadAssess.degree_level_score ?? acadAssess.score) ?? acadScore;
    const fieldRel    = normaliseScore(acadAssess.field_relevance     ?? acadAssess.score) ?? acadScore;
    const reqsMet     = normaliseScore(acadAssess.requirements_met    ?? acadAssess.score) ?? acadScore;
    const confidence  = acadScore >= 70 ? 'High' : acadScore >= 45 ? 'Medium' : 'Low';
    categories.push({
      name: 'ACADEMIC',
      label: 'Academic',
      score: acadScore,
      weight: 15,
      benchmark: BENCHMARK,
      confidence,
      subScores: [
        { label: 'Degree level',     value: degreeLevel },
        { label: 'Field relevance',  value: fieldRel },
        { label: 'Requirements met', value: reqsMet },
      ],
    });
  }

  return categories.map(c => ({
    ...c,
    percentile: estimatePercentile(c.score),
    vsBaseline: c.score - c.benchmark,
    contribution: Math.round(c.score * c.weight / 100),
  }));
}

function computeScoreDrivers(evaluation) {
  const positive = [];
  const negative = [];

  const skills = evaluation.skill_matches || [];
  const strongCritical  = skills.filter(s => s.match_level === 'strong'  && s.importance === 'critical');
  const missingCritical = skills.filter(s => s.match_level === 'missing' && s.importance === 'critical');
  const strongImportant = skills.filter(s => s.match_level === 'strong'  && s.importance === 'important');
  const missingImportant = skills.filter(s => s.match_level === 'missing' && s.importance === 'important');

  if (strongCritical.length > 0)
    positive.push(`${strongCritical.length} critical skill${strongCritical.length > 1 ? 's' : ''} fully matched (${strongCritical.slice(0,3).map(s => s.skill).join(', ')})`);
  if (strongImportant.length > 0)
    positive.push(`${strongImportant.length} important skill${strongImportant.length > 1 ? 's' : ''} confirmed`);
  if (missingCritical.length > 0)
    negative.push(`${missingCritical.length} critical skill${missingCritical.length > 1 ? 's' : ''} not found (${missingCritical.slice(0,2).map(s => s.skill).join(', ')})`);
  if (missingImportant.length > 0)
    negative.push(`${missingImportant.length} important skill${missingImportant.length > 1 ? 's' : ''} missing`);

  const expScore = normaliseScore(evaluation.experience_assessment?.score);
  if (expScore != null) {
    if (expScore >= 75) positive.push('Strong experience match for the role');
    else if (expScore < 45) negative.push('Experience level below role requirements');
  }

  const gaps = evaluation.gaps || [];
  const critGaps = gaps.filter(g => g.severity === 'critical');
  if (critGaps.length > 0)
    negative.push(`${critGaps.length} critical gap${critGaps.length > 1 ? 's' : ''} identified`);

  const strengths = evaluation.strengths || [];
  if (strengths.length >= 3)
    positive.push(`${strengths.length} notable strengths identified`);

  const acadScore = normaliseScore(evaluation.education_assessment?.score);
  if (acadScore != null && acadScore >= 80)
    positive.push('Educational background exceeds requirements');
  else if (acadScore != null && acadScore < 40)
    negative.push('Educational background below requirements');

  return { positive: positive.slice(0, 4), negative: negative.slice(0, 4) };
}
