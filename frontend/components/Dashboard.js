'use client';

import { useState, useEffect } from 'react';
import { getDashboardStats } from '../lib/api';

export default function Dashboard() {
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadStats();
  }, []);

  async function loadStats() {
    try {
      const data = await getDashboardStats();
      setStats(data);
    } catch (err) {
      console.error('Failed to load stats:', err);
    } finally {
      setLoading(false);
    }
  }

  if (loading) {
    return (
      <div className="loading-overlay">
        <div className="loading-spinner lg" />
        <span>Loading dashboard...</span>
      </div>
    );
  }

  if (!stats) {
    return (
      <div className="empty-state">
        <div className="empty-state-icon">⚠️</div>
        <div className="empty-state-text">Could not load dashboard. Is the backend running?</div>
      </div>
    );
  }

  const statCards = [
    { label: 'Active Requisitions', value: stats.active_requisitions, icon: '📋' },
    { label: 'Total Candidates', value: stats.total_candidates, icon: '👥' },
    { label: 'Evaluated', value: stats.evaluated_candidates, icon: '✅' },
    { label: 'Pending Review', value: stats.pending_candidates, icon: '⏳' },
    { label: 'Flagged', value: stats.flagged_candidates, icon: '🚩' },
    {
      label: 'Avg Confidence',
      value: stats.avg_confidence ? `${(stats.avg_confidence * 100).toFixed(0)}%` : '—',
      icon: '🎯',
    },
  ];

  const recDist = stats.recommendation_distribution || {};

  return (
    <div>
      <div className="page-header">
        <div>
          <h1 className="page-title">Dashboard</h1>
          <p className="page-subtitle">Overview of your hiring pipeline</p>
        </div>
      </div>

      <div className="stats-grid">
        {statCards.map((card) => (
          <div key={card.label} className="stat-card">
            <div className="stat-icon">{card.icon}</div>
            <div className="stat-label">{card.label}</div>
            <div className="stat-value">{card.value}</div>
          </div>
        ))}
      </div>

      {/* Recommendation Distribution */}
      <div className="card" style={{ marginTop: 'var(--space-lg)' }}>
        <div className="card-header">
          <div>
            <div className="card-title">Recommendation Distribution</div>
            <div className="card-subtitle">Across all evaluated candidates</div>
          </div>
        </div>
        <div style={{ display: 'flex', gap: 'var(--space-lg)', flexWrap: 'wrap' }}>
          {[
            { key: 'strong_hire', label: 'Strong Hire', class: 'badge-strong-hire' },
            { key: 'hire', label: 'Hire', class: 'badge-hire' },
            { key: 'consider', label: 'Consider', class: 'badge-consider' },
            { key: 'no_hire', label: 'No Hire', class: 'badge-no-hire' },
          ].map((item) => (
            <div key={item.key} style={{ textAlign: 'center' }}>
              <div style={{ fontSize: '1.5rem', fontWeight: 700, marginBottom: '4px' }}>
                {recDist[item.key] || 0}
              </div>
              <span className={`badge ${item.class}`}>{item.label}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
