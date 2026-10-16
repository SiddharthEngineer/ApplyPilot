import { useState, type ReactNode } from 'react';

// Parts shared by the Preferences and Profile pages.

/** Free-text tags: type and press Enter (or a comma) to add, × to remove. */
export function TagInput({ label, values, placeholder, onChange }: {
  label: string; values: string[]; placeholder?: string; onChange: (v: string[]) => void;
}) {
  const [text, setText] = useState('');
  const add = () => {
    const parts = text.split(',').map((t) => t.trim()).filter((t) => t && !values.includes(t));
    if (parts.length) onChange([...values, ...parts]);
    setText('');
  };
  return (
    <div className="tag-input">
      {values.map((v) => (
        <span key={v} className="chip">
          {v}
          <button type="button" className="chip-remove" aria-label={`Remove ${v}`}
            onClick={() => onChange(values.filter((x) => x !== v))}>×</button>
        </span>
      ))}
      <input aria-label={label} value={text} placeholder={placeholder}
        onChange={(e) => setText(e.target.value)} onBlur={add}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ',') {
            e.preventDefault();
            add();
          }
        }} />
    </div>
  );
}

/** A card of rows on a settings page; `advanced` ones start folded. */
export function Group({ title, hint, advanced = false, children }: {
  title: string; hint?: string; advanced?: boolean; children: ReactNode;
}) {
  const body = <>{hint && <p className="muted group-hint">{hint}</p>}<div className="pref-rows">{children}</div></>;
  if (advanced) {
    return (
      <details className="card pref-group">
        <summary><h2>{title}</h2></summary>
        {body}
      </details>
    );
  }
  return <section className="card pref-group"><h2>{title}</h2>{body}</section>;
}
