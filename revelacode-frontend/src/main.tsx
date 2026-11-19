import React from 'react';
import ReactDOM from 'react-dom/client';
import LegendWorkbench from './components/LegendWorkbench';
import './components/LegendWorkbench.css';

const root = ReactDOM.createRoot(document.getElementById('root')!);
root.render(
  <React.StrictMode>
    <LegendWorkbench />
  </React.StrictMode>
);
