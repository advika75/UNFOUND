import { useEffect, useId, useRef, useState } from "react";

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const MIN_PASSWORD_LENGTH = 8;

function validate(mode, { email, password, confirmPassword }) {
  const errors = {};
  if (!email.trim()) errors.email = "Enter your email address.";
  else if (!EMAIL_PATTERN.test(email.trim())) errors.email = "That doesn't look like a valid email address.";

  if (!password) errors.password = "Enter a password.";
  else if (mode === "signup" && password.length < MIN_PASSWORD_LENGTH)
    errors.password = `Use at least ${MIN_PASSWORD_LENGTH} characters.`;

  if (mode === "signup" && password && confirmPassword !== password)
    errors.confirmPassword = "Passwords don't match.";

  return errors;
}

export default function AuthModal({ open, onClose, onSignIn, onSignUp }) {
  const [mode, setMode] = useState("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [fieldErrors, setFieldErrors] = useState({});
  const [formError, setFormError] = useState("");
  const [formNotice, setFormNotice] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const emailInputRef = useRef(null);
  const headingId = useId();

  useEffect(() => {
    if (!open) return;
    setMode("signin");
    setEmail("");
    setPassword("");
    setConfirmPassword("");
    setFieldErrors({});
    setFormError("");
    setFormNotice("");
    setSubmitting(false);
    const timer = setTimeout(() => emailInputRef.current?.focus(), 10);
    return () => clearTimeout(timer);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open, onClose]);

  if (!open) return null;

  async function handleSubmit(event) {
    event.preventDefault();
    if (submitting) return;
    const errors = validate(mode, { email, password, confirmPassword });
    setFieldErrors(errors);
    setFormError("");
    setFormNotice("");
    if (Object.keys(errors).length > 0) return;

    setSubmitting(true);
    const result = mode === "signin" ? await onSignIn({ email: email.trim(), password }) : await onSignUp({ email: email.trim(), password });
    setSubmitting(false);

    if (result?.error) {
      setFormError(result.error);
      return;
    }
    if (result?.notice) {
      setFormNotice(result.notice);
      return;
    }
    // A clean sign-in (no notice, no error) closes the modal and lets the caller's
    // pending action (e.g. the save that triggered this) complete.
  }

  const switchMode = (next) => {
    setMode(next);
    setFieldErrors({});
    setFormError("");
    setFormNotice("");
  };

  return (
    <div
      className="auth-modal-overlay"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className="auth-modal" role="dialog" aria-modal="true" aria-labelledby={headingId}>
        <button type="button" className="auth-modal-close" aria-label="Close" onClick={onClose}>
          ✕
        </button>
        <p className="kicker">{mode === "signin" ? "WELCOME BACK" : "JOIN UNFOUND"}</p>
        <h2 id={headingId}>{mode === "signin" ? "Sign in to save what you find." : "Create your account."}</h2>
        <p className="auth-modal-subtitle">
          {mode === "signin"
            ? "Your saves, boards and preferences follow you back here."
            : "One place for everything you're keeping an eye on."}
        </p>

        <form onSubmit={handleSubmit} noValidate>
          <label className="auth-field">
            <span>Email</span>
            <input
              ref={emailInputRef}
              type="email"
              autoComplete="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              aria-invalid={Boolean(fieldErrors.email)}
              className={fieldErrors.email ? "has-error" : ""}
            />
            {fieldErrors.email && <small className="auth-field-error">{fieldErrors.email}</small>}
          </label>

          <label className="auth-field">
            <span>Password</span>
            <input
              type="password"
              autoComplete={mode === "signin" ? "current-password" : "new-password"}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              aria-invalid={Boolean(fieldErrors.password)}
              className={fieldErrors.password ? "has-error" : ""}
            />
            {fieldErrors.password && <small className="auth-field-error">{fieldErrors.password}</small>}
          </label>

          {mode === "signup" && (
            <label className="auth-field">
              <span>Confirm password</span>
              <input
                type="password"
                autoComplete="new-password"
                value={confirmPassword}
                onChange={(event) => setConfirmPassword(event.target.value)}
                aria-invalid={Boolean(fieldErrors.confirmPassword)}
                className={fieldErrors.confirmPassword ? "has-error" : ""}
              />
              {fieldErrors.confirmPassword && <small className="auth-field-error">{fieldErrors.confirmPassword}</small>}
            </label>
          )}

          {formError && <p className="auth-form-error">{formError}</p>}
          {formNotice && <p className="auth-form-notice">{formNotice}</p>}

          <button type="submit" className="auth-submit" disabled={submitting}>
            {submitting ? "Working…" : mode === "signin" ? "Sign in" : "Create account"}
          </button>
        </form>

        <p className="auth-modal-switch">
          {mode === "signin" ? (
            <>Don't have an account? <button type="button" onClick={() => switchMode("signup")}>Create one</button></>
          ) : (
            <>Already have an account? <button type="button" onClick={() => switchMode("signin")}>Sign in</button></>
          )}
        </p>
      </div>
    </div>
  );
}
