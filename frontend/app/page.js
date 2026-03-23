'use client';

import { useState } from 'react';
import Sidebar from '../components/Sidebar';
import Dashboard from '../components/Dashboard';
import Requisitions from '../components/Requisitions';
import Evaluations from '../components/Evaluations';

export default function Home() {
  const [activePage, setActivePage] = useState('dashboard');
  const [selectedRequisition, setSelectedRequisition] = useState(null);

  function handleNavigate(page) {
    setActivePage(page);
    setSelectedRequisition(null);
  }

  function handleSelectRequisition(req) {
    setSelectedRequisition(req);
    setActivePage('evaluations');
  }

  function handleBackToRequisitions() {
    setSelectedRequisition(null);
    setActivePage('requisitions');
  }

  function renderPage() {
    if (activePage === 'evaluations' && selectedRequisition) {
      return (
        <Evaluations
          requisition={selectedRequisition}
          onBack={handleBackToRequisitions}
        />
      );
    }

    switch (activePage) {
      case 'dashboard':
        return <Dashboard />;
      case 'requisitions':
        return <Requisitions onSelectRequisition={handleSelectRequisition} />;
      case 'evaluations':
        return (
          <Requisitions onSelectRequisition={handleSelectRequisition} />
        );
      default:
        return <Dashboard />;
    }
  }

  return (
    <div className="app-layout">
      <Sidebar activePage={activePage} onNavigate={handleNavigate} />
      <main className="main-content">
        {renderPage()}
      </main>
    </div>
  );
}
