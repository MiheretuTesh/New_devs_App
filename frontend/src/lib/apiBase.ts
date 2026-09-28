// API base URL utilities

export const getApiBase = (): string => {
  const configured = import.meta.env.VITE_BACKEND_URL;
  if (configured) return configured;

  // .env is in .dockerignore, so a Docker build has no VITE_BACKEND_URL. Without a local
  // default the profile calls went to the frontend's own nginx (:3000), which answered
  // with index.html instead of JSON. Same default secureApi already uses.
  if (typeof window !== 'undefined' &&
    (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1')) {
    return 'http://localhost:8000';
  }

  return '';
};

export const getApiUrl = (path: string): string => {
  const base = getApiBase();
  return `${base}${path}`;
};
