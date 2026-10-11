import React from 'react';
import { createRoot } from 'react-dom/client';
import '@fontsource-variable/figtree';
import './styles/index.css';
import { App } from './app/App';

createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>);
