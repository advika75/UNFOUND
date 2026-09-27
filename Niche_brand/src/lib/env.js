// A missing or mis-set required env var should fail loudly at startup, not silently
// fall back to localhost or a hardcoded live project (the latter would write to real data).
export function requireEnv(name) {
  const value = import.meta.env[name];
  if (!value) {
    throw new Error(
      `Missing required environment variable ${name}. Set it in .env for local development, or in the deployment's environment variables.`
    );
  }
  return value;
}
