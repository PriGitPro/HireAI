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
                      {evalProgress.decisionStats && (
                        <span>→ {formatRecommendation(evalProgress.decisionStats.recommendation)} ({(evalProgress.decisionStats.confidence * 100).toFixed(0)}% confidence)</span>
                      )}
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
                        <div className="score-bar" style={{ marginTop: 8 }}>
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

                  {/* Explanation */}
                  {evaluation.explanation && (
                    <div className="card">
                      <div className="card-title" style={{ marginBottom: 'var(--space-md)' }}>💡 Explanation</div>
                      <p style={{ fontSize: '0.9rem', lineHeight: 1.7, color: 'var(--text-secondary)' }}>
                        {evaluation.explanation}
                      </p>
                    </div>
                  )}

                  {/* Skill Matches */}
                  {evaluation.skill_matches && evaluation.skill_matches.length > 0 && (
                    <div className="card">
                      <div className="card-title" style={{ marginBottom: 'var(--space-md)' }}>🎯 Skill Assessment</div>
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
                      <div style={{ marginTop: 'var(--space-md)', display: 'flex', gap: 'var(--space-lg)', fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                        <span>✓ Strong</span>
                        <span>◐ Partial</span>
                        <span>○ Weak</span>
                        <span>✗ Missing</span>
                      </div>
                    </div>
                  )}

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
                    {evaluation.gaps && evaluation.gaps.length > 0 && (
                      <div className="card">
                        <div className="card-title" style={{ marginBottom: 'var(--space-md)', color: 'var(--danger)' }}>
                          ⚠️ Gaps & Risks
                        </div>
                        <ul style={{ listStyle: 'none', display: 'flex', flexDirection: 'column', gap: 'var(--space-sm)' }}>
                          {evaluation.gaps.map((g, i) => {
                            // Support both legacy string and new {skill, severity, description, impact} object
                            const isObj = typeof g === 'object';
                            const label = isObj ? g.description || g.skill : g;
                            const severity = isObj ? g.severity : null;
                            const impact = isObj ? g.impact : null;
                            return (
                              <li key={i} style={{
                                fontSize: '0.85rem',
                                padding: 'var(--space-sm)',
                                background: severity === 'critical' ? 'rgba(239,68,68,0.12)' : 'var(--danger-bg)',
                                borderRadius: 'var(--radius-sm)',
                                borderLeft: severity === 'critical' ? '3px solid var(--danger)' : severity === 'important' ? '3px solid var(--warning)' : '3px solid var(--border-subtle)',
                                color: 'var(--text-secondary)',
                              }}>
                                <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-xs)' }}>
                                  {severity === 'critical' && <span title="Critical gap" style={{ fontSize: '0.7rem', fontWeight: 700, color: 'var(--danger)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>CRITICAL</span>}
                                  {severity === 'important' && <span title="Important gap" style={{ fontSize: '0.7rem', fontWeight: 600, color: 'var(--warning)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>IMPORTANT</span>}
                                  {severity === 'minor' && <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)', textTransform: 'uppercase' }}>MINOR</span>}
                                  <span>🔸 {label}</span>
                                </div>
                                {impact && (
                                  <div style={{ marginTop: 2, fontSize: '0.75rem', color: 'var(--text-muted)' }}>{impact}</div>
                                )}
                              </li>
                            );
                          })}
                        </ul>
                      </div>
                    )}
                  </div>

                  {/* Decision Trace — signal-based steps */}
                  {evaluation.decision_trace && evaluation.decision_trace.length > 0 && (
                    <div className="card">
                      <div className="card-title" style={{ marginBottom: 'var(--space-lg)' }}>🔍 Decision Trace</div>
                      <div className="decision-trace">
                        {evaluation.decision_trace.map((step, i) => (
                          <div key={i} className="trace-step" style={{ animationDelay: `${i * 0.1}s` }}>
                            <div className={`trace-dot ${step.impact || 'neutral'}`} />
                            <div className="trace-content">
                              {/* Support both old {action} and new {signal} field */}
                              <div className="trace-action">
                                Step {step.step}: {step.signal || step.action}
                                {step.weight != null && (
                                  <span style={{ marginLeft: 8, fontSize: '0.7rem', color: 'var(--text-muted)', fontWeight: 400 }}>
                                    weight {(step.weight * 100).toFixed(0)}%
                                  </span>
                                )}
                              </div>
                              <div className="trace-finding">{step.finding}</div>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

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
                    <option value="maybe">Maybe</option>
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
    case 'maybe': return 'badge-maybe';
    case 'no_hire': return 'badge-no-hire';
    default: return 'badge-info';
  }
}

function getRecommendationColor(rec) {
  switch (rec) {
    case 'strong_hire': return 'rgba(16, 185, 129, 0.3)';
    case 'hire': return 'rgba(52, 211, 153, 0.3)';
    case 'maybe': return 'rgba(245, 158, 11, 0.3)';
    case 'no_hire': return 'rgba(239, 68, 68, 0.3)';
    default: return 'var(--border-subtle)';
  }
}

function formatRecommendation(rec) {
  switch (rec) {
    case 'strong_hire': return 'Strong Hire';
    case 'hire': return 'Hire';
    case 'maybe': return 'Maybe';
    case 'no_hire': return 'No Hire';
    default: return rec || '—';
  }
}

function getScoreLevel(score) {
  if (score >= 70) return 'high';
  if (score >= 40) return 'medium';
  return 'low';
}
