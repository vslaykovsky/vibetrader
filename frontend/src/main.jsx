import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import App from './App';
import { AuthProvider } from './AuthContext';
import { ThemeProvider } from './ThemeContext';
import { TimeZoneProvider } from './TimeZoneContext.jsx';
import './styles.css';

const app = (
  <BrowserRouter>
    <ThemeProvider>
      <AuthProvider>
        <TimeZoneProvider>
          <App />
        </TimeZoneProvider>
      </AuthProvider>
    </ThemeProvider>
  </BrowserRouter>
);

ReactDOM.createRoot(document.getElementById('root')).render(
  import.meta.env.DEV ? app : <React.StrictMode>{app}</React.StrictMode>,
);
