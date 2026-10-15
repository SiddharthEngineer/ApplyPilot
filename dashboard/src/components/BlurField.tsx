import { useEffect, useState } from 'react';

interface Props {
  label: string;
  value: string | null;
  onSave: (value: string | null) => void;
  multiline?: boolean;
  type?: string;
  placeholder?: string;
  className?: string;
}

/** A labelled input (or textarea) that saves on blur, only when the text changed. Empty saves as null. */
export default function BlurField({ label, value, onSave, multiline, type = 'text', placeholder, className = 'field' }: Props) {
  const [text, setText] = useState(value ?? '');
  useEffect(() => setText(value ?? ''), [value]);
  const save = () => {
    if (text !== (value ?? '')) onSave(text.trim() ? text : null);
  };
  return (
    <label className={className}>
      <span>{label}</span>
      {multiline ? (
        <textarea rows={3} value={text} placeholder={placeholder} onChange={(e) => setText(e.target.value)} onBlur={save} />
      ) : (
        <input type={type} value={text} placeholder={placeholder} onChange={(e) => setText(e.target.value)} onBlur={save} />
      )}
    </label>
  );
}
