# Plan: Contact form with separate fields
**Status:** ❌ Not started
**Risk:** review (dashboard UI; merges under AGENT_MERGE_POLICY=all)
**Issue:** #49
**Depends on:** user-auth-8-cli

## Goal
Contacts have a proper form with separate fields: Name (required), Email, Phone, LinkedIn, then optional Company,
Role and Notes. The Contacts page gets a "New contact" button that opens that form. On a job page, "Add contact" keeps
the search-as-you-type picker for linking an existing contact. "Create new" now opens the same form, prefilled with the
typed name and the job's company, instead of creating a name-only contact straight away.

## Context
- `dashboard/src/components/ContactsPanel.tsx:11-90`: `ContactPicker` (search, relation select, "Create '<name>'" button
  that posts `{contact: {name, company}}`). `:91-138` `ContactCard` (inline edit). `:139-` the panel.
- `dashboard/src/pages/ContactsPage.tsx:9-79`: `ContactEditor` with `BlurField`s (save on blur). `:80-151`: list and
  search; the empty state says "add them from a job page".
- `dashboard/src/api.ts:190-200`: `linkContact(key, {contact_id} | {contact}, relation)`, `createContact`, `listContacts`.
- `src/strelitzia/web/app.py:576-635`: contacts API. `ContactCreate` requires name; the other fields are optional already.
  Add server validation: email must contain "@", and LinkedIn must be an http(s) URL on linkedin.com when given (422 otherwise).
  Phone is free text, trimmed.
- `dashboard/src/components/BlurField.tsx`: the field component to reuse for edit mode.

## Tasks
### Task 1: API validation
**Runs:** unattended
**Files:** src/strelitzia/web/app.py (modify), tests/web/test_workspace_api.py (modify)
**What:** Pydantic validators on `ContactCreate`/`ContactPatch` for email and linkedin_url as above. Empty strings become null.
**Validate:** `pytest tests/web/test_workspace_api.py -q` → passes, with 422 for a bad email or a non-LinkedIn URL.
**Status:** ✅ 2026-10-16 (this commit)

### Task 2: ContactForm and the Contacts page
**Runs:** unattended
**Files:** dashboard/src/components/ContactForm.tsx (new), dashboard/src/pages/ContactsPage.tsx (modify), dashboard/src/__tests__/ContactForm.test.tsx (new)
**What:** `ContactForm({initial, submitLabel, onSubmit, onCancel})`: labelled inputs (`type=email`, `tel`, `url`), Name
required, client-side checks matching the API, the API's 422 message shown inline. Contacts page: "New contact" button
→ form → created contact selected in the editor. Empty-state text: "No contacts yet. Add one with New contact."
**Validate:** `cd dashboard && npm test -- --run` → passes.
**Status:** ❌

### Task 3: create-from-job uses the form
**Runs:** unattended
**Files:** dashboard/src/components/ContactsPanel.tsx (modify), dashboard/src/__tests__/JobPage.test.tsx (modify), src/strelitzia/web/static (rebuild)
**What:** In `ContactPicker`, "Create new" (shown whenever text is typed, and also as a plain "New contact" link) opens
`ContactForm` prefilled with `{name, company}`. Submitting calls `linkContact(key, {contact: fields}, relation)`.
Linking an existing contact is unchanged.
**Validate:** `cd dashboard && npm test -- --run && npm run build` → passes.
**Status:** ❌

## Validation
- `scripts/validate.sh --full` passes.

## Human test steps (preprod)
1. Contacts → New contact: fill in all fields, save; it appears in the list.
2. A job → Add contact: type part of that name and link it. Type a new name → Create new → fill in email → it's linked.

## Decisions
1. Name is the only required field (user, 2026-10-16). Company, role and notes stay as optional fields.
