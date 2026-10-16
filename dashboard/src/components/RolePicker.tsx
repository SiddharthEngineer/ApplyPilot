import { useState } from 'react';
import { roleLabel } from '../labels';

/** Target roles: the suggested titles of each chosen job field as chips to tick, plus custom titles typed in. */
export default function RolePicker({ fields, rolesByField, value, onChange }: {
  fields: string[]; rolesByField: Record<string, string[]>; value: string[]; onChange: (v: string[]) => void;
}) {
  const [text, setText] = useState('');
  const groups = fields.map((f) => ({ field: f, roles: rolesByField[f] ?? [] })).filter((g) => g.roles.length);
  const shown = new Set(groups.flatMap((g) => g.roles));
  // Anything not offered as a suggestion right now (typed in, or from a field no longer chosen) is removable.
  const custom = value.filter((v) => !shown.has(v));
  const toggle = (role: string) => onChange(value.includes(role) ? value.filter((v) => v !== role) : [...value, role]);
  const add = () => {
    const typed = text.trim();
    setText('');
    if (!typed) return;
    const same = (v: string) => v.toLowerCase() === typed.toLowerCase();
    if (value.some(same)) return;
    onChange([...value, [...shown].find(same) ?? typed]);
  };

  return (
    <div className="role-picker">
      {groups.map((g) => (
        <div key={g.field} className="chips pick" role="group" aria-label={`${roleLabel(g.field)} roles`}>
          <span className="muted role-field">{roleLabel(g.field)}</span>
          {g.roles.map((r) => (
            <button key={r} type="button" className="chip-toggle" aria-pressed={value.includes(r)}
              onClick={() => toggle(r)}>{r}</button>
          ))}
        </div>
      ))}
      <div className="tag-input">
        {custom.map((v) => (
          <span key={v} className="chip">
            {v}
            <button type="button" className="chip-remove" aria-label={`Remove ${v}`}
              onClick={() => onChange(value.filter((x) => x !== v))}>×</button>
          </span>
        ))}
        <input aria-label="Add other role" value={text} placeholder="Add other role; Enter to add"
          onChange={(e) => setText(e.target.value)} onBlur={add}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault();
              add();
            }
          }} />
      </div>
    </div>
  );
}
