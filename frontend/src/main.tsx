import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';
import './styles/base.css';

/**
 * Where the app starts.
 *
 * The theme has already been settled by a small script in the page itself, so
 * by the time anything here runs the document is in the right one and there
 * is nothing to correct.
 */
const root = document.getElementById('root');
if (!root) throw new Error('the page has no #root to render into');

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
