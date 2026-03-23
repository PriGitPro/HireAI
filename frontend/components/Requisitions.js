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
                    <button
                      className="btn btn-danger btn-sm"
                      onClick={(e) => handleDelete(req.id, e)}
                    >
                      🗑
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
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
