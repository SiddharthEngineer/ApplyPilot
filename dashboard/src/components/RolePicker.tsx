import { roleLabel } from '../labels';
import { TagInput } from './FormParts';

const key = (role: string) => role.trim().toLowerCase();

/** Target roles: the suggested titles of each chosen job field as chips to tick, plus custom titles typed in.
 * Titles match case-insensitively, and a typed title that is a suggestion of any field is stored as the suggestion. */
export default function RolePicker({ fields, rolesByField, value, onChange }: {
  fields: string[]; rolesByField: Record<string, string[]>; value: string[]; onChange: (v: string[]) => void;
}) {
  const groups = fields.map((f) => ({ field: f, roles: rolesByField[f] ?? [] })).filter((g) => g.roles.length);
  const shown = new Set(groups.flatMap((g) => g.roles.map(key)));
  const canonical = new Map(Object.values(rolesByField).flat().map((r) => [key(r), r]));
  const ticked = new Set(value.map(key));
  // Anything not offered as a suggestion right now (typed in, or from a field no longer chosen) is removable.
  const custom = value.filter((v) => !shown.has(key(v)));
  const toggle = (role: string) => onChange(ticked.has(key(role))
    ? value.filter((v) => key(v) !== key(role)) : [...value, role]);
  const editCustom = (next: string[]) => {
    const removed = new Set(custom.filter((v) => !next.includes(v)));
    const out = value.filter((v) => !removed.has(v));
    for (const typed of next.filter((v) => !custom.includes(v))) {
      if (!out.some((v) => key(v) === key(typed))) out.push(canonical.get(key(typed)) ?? typed);
    }
    if (removed.size || out.length !== value.length) onChange(out);
  };

  return (
    <div className="role-picker" role="group" aria-label="Target roles">
      {groups.map((g) => (
        <div key={g.field} className="chips pick" role="group" aria-label={`${roleLabel(g.field)} roles`}>
          <span className="muted role-field">{roleLabel(g.field)}</span>
          {g.roles.map((r) => (
            <button key={r} type="button" className="chip-toggle" aria-pressed={ticked.has(key(r))}
              onClick={() => toggle(r)}>{r}</button>
          ))}
        </div>
      ))}
      <TagInput label="Add other role" values={custom} placeholder="Add other role; Enter to add"
        onChange={editCustom} />
    </div>
  );
}
