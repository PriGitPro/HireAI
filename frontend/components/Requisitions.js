'use client';

import { useState, useEffect } from 'react';
import {
  getRequisitions,
  createRequisition,
  deleteRequisition,
} from '../lib/api';

export default function Requisitions({ onSelectRequisition }) {
  const [requisitions, setRequisitions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [creating, setCreating] = useState(false);
  const [viewingJD, setViewingJD] = useState(null); // req object whose JD is open
  const [formData, setFormData] = useState({
    title: '',
    department: '',
    location: '',
    employment_type: 'Full-time',
    description_raw: '',
  });

  useEffect(() => {
    loadRequisitions();
  }, []);

  async function loadRequisitions() {
    setLoading(true);
    try {
      const data = await getRequisitions();
      setRequisitions(data.items || []);
    } catch (err) {
      console.error('Failed to load requisitions:', err);
    } finally {
      setLoading(false);
    }
  }

  async function handleCreate(e) {
    e.preventDefault();
    if (!formData.title || !formData.description_raw) return;
    setCreating(true);
    try {
      await createRequisition(formData);
      setShowCreate(false);
      setFormData({
        title: '',
        department: '',
        location: '',
        employment_type: 'Full-time',
        description_raw: '',
      });
      await loadRequisitions();
    } catch (err) {
      alert(`Failed to create: ${err.message}`);
    } finally {
      setCreating(false);
    }
  }

  async function handleDelete(id, e) {
    e.stopPropagation();
    if (!confirm('Delete this requisition and all associated data?')) return;
    try {
      await deleteRequisition(id);
      await loadRequisitions();
    } catch (err) {
      alert(`Delete failed: ${err.message}`);
    }
  }

  return (
    <div>
      <div className="page-header">
        <div>
          <h1 className="page-title">Job Requisitions</h1>
          <p className="page-subtitle">Create and manage open positions</p>
        </div>
        <button className="btn btn-primary" onClick={() => setShowCreate(true)}>
          ＋ New Requisition
        </button>
      </div>

      {loading ? (
        <div className="loading-overlay">
          <div className="loading-spinner lg" />
          <span>Loading requisitions...</span>
        </div>
      ) : requisitions.length === 0 ? (
        <div className="empty-state">
          <div className="empty-state-icon">📋</div>
          <div className="empty-state-text">
            No requisitions yet. Create your first job opening to get started.
          </div>
          <button className="btn btn-primary" onClick={() => setShowCreate(true)}>
            Create Requisition
          </button>
        </div>
      ) : (
        <div className="table-container">
          <table className="data-table">
            <thead>
              <tr>
                <th>Title</th>
                <th>Department</th>
                <th>Location</th>
                <th>Type</th>
                <th>Candidates</th>
                <th>Status</th>
                <th>Created</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {requisitions.map((req) => (
                <tr
                  key={req.id}
                  onClick={() => onSelectRequisition(req)}
                  style={{ cursor: 'pointer' }}
                >
                  <td style={{ fontWeight: 600, color: 'var(--text-primary)' }}>
                    {req.title}
                    <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: 2 }}>
                      {req.id}
                    </div>
                  </td>
                  <td>{req.department || '—'}</td>
                  <td>{req.location || '—'}</td>
                  <td>{req.employment_type}</td>
                  <td>
                    <span className="badge badge-info">{req.candidate_count}</span>
                  </td>
                  <td>
                    <span className={`badge ${req.status === 'active' ? 'badge-success' : 'badge-warning'}`}>
                      {req.status}
                    </span>
                  </td>
                  <td style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                    {new Date(req.created_at).toLocaleDateString()}
                  </td>
                  <td>
                    <div style={{ display: 'flex', gap: 'var(--space-xs)' }}>
                      <button
                        className="btn btn-secondary btn-sm"
                        title="View job description"
                        onClick={(e) => { e.stopPropagation(); setViewingJD(req); }}
                      >
                        📋
                      </button>
                      <button
                        className="btn btn-danger btn-sm"
                        onClick={(e) => handleDelete(req.id, e)}
                      >
                        🗑
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* View JD Modal */}
      {viewingJD && (
        <div className="modal-overlay" onClick={() => setViewingJD(null)}>
          <div className="modal" style={{ maxWidth: 720, width: '95vw' }} onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <div>
                <h2 className="modal-title">{viewingJD.title}</h2>
                <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginTop: 2 }}>
                  {[viewingJD.department, viewingJD.location, viewingJD.employment_type].filter(Boolean).join(' · ')}
                  {' · '}
                  <span style={{ fontFamily: 'var(--font-mono)' }}>{viewingJD.id}</span>
                </div>
              </div>
              <button className="modal-close" onClick={() => setViewingJD(null)}>✕</button>
            </div>
            <div className="modal-body">
              {/* Skills extracted */}
              {viewingJD.required_skills && viewingJD.required_skills.length > 0 && (
                <div style={{ marginBottom: 'var(--space-lg)' }}>
                  <div style={{ fontSize: '0.75rem', fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 'var(--space-sm)' }}>
                    Extracted Skills ({viewingJD.required_skills.length})
                  </div>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--space-xs)' }}>
                    {viewingJD.required_skills.map((s, i) => (
                      <span
                        key={i}
                        className={`badge ${s.importance === 'critical' ? 'badge-danger' : s.importance === 'important' ? 'badge-warning' : 'badge-info'}`}
                        style={{ fontSize: '0.72rem' }}
                      >
                        {s.name}
                        <span style={{ opacity: 0.65, marginLeft: 4 }}>{s.importance === 'critical' ? '●●●' : s.importance === 'important' ? '●●' : '●'}</span>
                      </span>
                    ))}
                  </div>
                  <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)', marginTop: 'var(--space-xs)' }}>
                    ●●● critical &nbsp;·&nbsp; ●● important &nbsp;·&nbsp; ● secondary
                  </div>
                </div>
              )}

              {/* Experience & Education summary */}
              {(viewingJD.experience_requirements || viewingJD.education_requirements) && (
                <div style={{ display: 'flex', gap: 'var(--space-md)', marginBottom: 'var(--space-lg)' }}>
                  {viewingJD.experience_requirements?.min_years != null && (
                    <div style={{ padding: 'var(--space-sm) var(--space-md)', background: 'var(--bg-glass)', borderRadius: 'var(--radius-sm)', fontSize: '0.8rem' }}>
                      <span style={{ color: 'var(--text-muted)' }}>Experience: </span>
                      <strong>{viewingJD.experience_requirements.min_years}+ yrs</strong>
                    </div>
                  )}
                  {viewingJD.education_requirements?.min_level && (
                    <div style={{ padding: 'var(--space-sm) var(--space-md)', background: 'var(--bg-glass)', borderRadius: 'var(--radius-sm)', fontSize: '0.8rem' }}>
                      <span style={{ color: 'var(--text-muted)' }}>Education: </span>
                      <strong>{viewingJD.education_requirements.min_level}</strong>
                    </div>
                  )}
                </div>
              )}

              {/* Raw JD text */}
              <div>
                <div style={{ fontSize: '0.75rem', fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 'var(--space-sm)', display: 'flex', justifyContent: 'space-between' }}>
                  <span>Raw Job Description</span>
                  <span style={{ fontWeight: 400 }}>{viewingJD.description_raw?.length.toLocaleString()} chars</span>
                </div>
                <textarea
                  readOnly
                  value={viewingJD.description_raw || ''}
                  style={{
                    width: '100%',
                    height: '340px',
                    fontFamily: 'var(--font-mono)',
                    fontSize: '0.75rem',
                    lineHeight: 1.6,
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
            </div>
            <div className="modal-footer">
              <button className="btn btn-secondary" onClick={() => setViewingJD(null)}>Close</button>
              <button
                className="btn btn-primary"
                onClick={() => { setViewingJD(null); onSelectRequisition(viewingJD); }}
              >
                View Candidates →
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Create Modal */}
      {showCreate && (
        <div className="modal-overlay" onClick={() => setShowCreate(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h2 className="modal-title">Create Job Requisition</h2>
              <button className="modal-close" onClick={() => setShowCreate(false)}>✕</button>
            </div>
            <form onSubmit={handleCreate}>
              <div className="modal-body">
                <div className="form-group">
                  <label className="form-label">Job Title *</label>
                  <input
                    className="form-input"
                    placeholder="e.g. Senior Software Engineer"
                    value={formData.title}
                    onChange={(e) => setFormData({ ...formData, title: e.target.value })}
                    required
                  />
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 'var(--space-md)' }}>
                  <div className="form-group">
                    <label className="form-label">Department</label>
                    <input
                      className="form-input"
                      placeholder="e.g. Engineering"
                      value={formData.department}
                      onChange={(e) => setFormData({ ...formData, department: e.target.value })}
                    />
                  </div>
                  <div className="form-group">
                    <label className="form-label">Location</label>
                    <input
                      className="form-input"
                      placeholder="e.g. Remote, NYC"
                      value={formData.location}
                      onChange={(e) => setFormData({ ...formData, location: e.target.value })}
                    />
                  </div>
                </div>
                <div className="form-group">
                  <label className="form-label">Employment Type</label>
                  <select
                    className="form-select"
                    value={formData.employment_type}
                    onChange={(e) => setFormData({ ...formData, employment_type: e.target.value })}
                  >
                    <option value="Full-time">Full-time</option>
                    <option value="Part-time">Part-time</option>
                    <option value="Contract">Contract</option>
                    <option value="Internship">Internship</option>
                  </select>
                </div>
                <div className="form-group">
                  <label className="form-label">Job Description *</label>
                  <textarea
                    className="form-textarea"
                    placeholder="Paste the full job description here. The AI will automatically extract skills, requirements, and responsibilities..."
                    value={formData.description_raw}
                    onChange={(e) => setFormData({ ...formData, description_raw: e.target.value })}
                    rows={8}
                    required
                  />
                </div>
              </div>
              <div className="modal-footer">
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={() => setShowCreate(false)}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="btn btn-primary"
                  disabled={creating || !formData.title || !formData.description_raw}
                >
                  {creating ? (
                    <>
                      <span className="loading-spinner" />
                      Creating...
                    </>
                  ) : (
                    'Create & Parse JD'
                  )}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
