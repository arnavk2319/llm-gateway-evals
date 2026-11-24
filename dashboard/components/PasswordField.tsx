"use client";

import { useState } from "react";

type Props = {
  name: string;
  label: string;
  autoComplete: "current-password" | "new-password";
  /** Show the length rule under the field and enforce it as the user types. */
  min?: number;
  hint?: string;
  autoFocus?: boolean;
};

const Check = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <circle cx="12" cy="12" r="10" />
    <path d="m9 12 2 2 4-4" />
  </svg>
);

/** A password input with a show/hide button and, for new passwords, a live length rule. The field name and form behaviour are unchanged. */
export function PasswordField({ name, label, autoComplete, min, hint, autoFocus }: Props) {
  const [show, setShow] = useState(false);
  const [value, setValue] = useState("");
  const id = `pw-${name}`;
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <div className="pw">
        <input
          className="input"
          id={id}
          name={name}
          type={show ? "text" : "password"}
          autoComplete={autoComplete}
          minLength={min}
          required
          autoFocus={autoFocus}
          value={value}
          onChange={(e) => setValue(e.target.value)}
        />
        <button type="button" onClick={() => setShow(!show)} aria-label={show ? "Hide password" : "Show password"}>
          {show ? "Hide" : "Show"}
        </button>
      </div>
      {min ? (
        <div className="rules">
          <div className={`rule ${value.length >= min ? "met" : ""}`}>
            <Check />
            At least {min} characters
          </div>
        </div>
      ) : null}
      {hint ? <span className="hint">{hint}</span> : null}
    </div>
  );
}
