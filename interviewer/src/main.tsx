import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import { CoverageStudy } from '../../validation/ui/components/CoverageStudy';
import './index.css';

// Validation studies mount as standalone entries, bypassing the interview app's
// phase machine entirely. ?phase=validation → the coverage task-rating study.
const isValidation =
  new URLSearchParams(window.location.search).get('phase') === 'validation';
const Root = isValidation ? CoverageStudy : App;

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <Root />
  </React.StrictMode>
);
