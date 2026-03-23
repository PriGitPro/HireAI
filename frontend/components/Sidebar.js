'use client';

import { useState, useEffect } from 'react';
import { getHealthCheck } from '../lib/api';

export default function Sidebar({ activePage, onNavigate }) {
  const [llmConnected, setLlmConnected] = useState(false);
  const [llmProvider, setLlmProvider] = useState('');

  useEffect(() => {
    checkHealth();
    const interval = setInterval(checkHealth, 30000);
    return () => clearInterval(interval);
  }, []);

  async function checkHealth() {
    try {
      const health = await getHealthCheck();
      setLlmConnected(health.llm_connected);
      setLlmProvider(health.llm_provider);
    } catch {
      setLlmConnected(false);
    }
  }

  const navItems = [
    { id: 'dashboard', icon: '📊', label: 'Dashboard' },
    { id: 'requisitions', icon: '📋', label: 'Requisitions' },
    { id: 'evaluations', icon: '🧠', label: 'Evaluations' },
  ];

  return (
    <aside className="sidebar">
      <div className="sidebar-header">
        <div className="sidebar-logo">
          <div className="sidebar-logo-icon">⚡</div>
          <div>
            <div className="sidebar-logo-text">HireAI</div>
            <div style={{ fontSize: '0.65rem', color: 'var(--text-muted)' }}>
              Intelligent Copilot
            </div>
          </div>
        </div>
      </div>

      <nav className="sidebar-nav">
        {navItems.map((item) => (
          <div
            key={item.id}
            className={`nav-item ${activePage === item.id ? 'active' : ''}`}
            onClick={() => onNavigate(item.id)}
          >
            <span className="nav-item-icon">{item.icon}</span>
            <span>{item.label}</span>
          </div>
        ))}
      </nav>

      <div className="sidebar-footer">
        <div className="llm-status">
          <span className={`llm-status-dot ${llmConnected ? 'connected' : ''}`} />
          <span>
            {llmConnected
              ? `${llmProvider} connected`
              : 'LLM disconnected'}
          </span>
        </div>
      </div>
    </aside>
  );
}
