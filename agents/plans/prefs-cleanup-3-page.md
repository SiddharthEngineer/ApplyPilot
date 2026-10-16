# Plan: Preferences cleanup, part 3: regrouped Preferences page
**Status:** ❌ Not started
**Risk:** review (dashboard UI)
**Issue:** #46
**Depends on:** prefs-cleanup-2-roles

## Goal
The Preferences page is only about the job the user wants. The groups are:
1. **Basics:** Job field (multi-select), then Target roles. Choosing fields shows that field's suggested roles as chips to tick,
   plus an "Add other" box. Then Salary, Location and Work mode.
2. **Role requirements:** Seniority, Employment type, Visa sponsorship, Security clearance, Travel (the old Role and
   Eligibility groups combined).
3. **Skills and industries:** Skills I want to use, Skills to avoid, Industries I like, Industries to avoid.
4. **Red flags:** checkboxes for the flags that should count against a job, each with a one-line explanation.

The "What the AI judges" group, Companies to skip, Education and Experience are gone. A line at the top links to the
Profile page: "Your experience, education and skills are on your Profile."

## Context
- `dashboard/src/pages/PreferencesPage.tsx:88-100` (`Group`), `:210-300` (the current groups: Basics, What the AI judges,
  Role, Eligibility, Companies to skip), `:167-200, 309-325` (save and the re-assess dialog; keep both, including the
  "all active jobs" checkbox from rescore-all).
- `dashboard/src/types.ts`: `Preferences`, `PreferencesResponse` (gets `options.roles_by_field` and the red-flag options).
- `dashboard/src/labels.ts`: category and flag labels. `dashboard/src/__tests__/PreferencesPage.test.tsx`: update it for the
  new groups; don't delete coverage of save/confirm.
- The API shapes come from prefs-cleanup-1 and -2.

## Tasks
### Task 1: regroup and remove
**Runs:** unattended
**Files:** dashboard/src/pages/PreferencesPage.tsx (modify), dashboard/src/types.ts (modify), dashboard/src/__tests__/PreferencesPage.test.tsx (modify)
**What:** The layout in the Goal. The Basics and Skills groups are open by default; the others are collapsible as today.
**Validate:** `cd dashboard && npm test -- --run` → passes.
**Status:** ❌

### Task 2: role picker
**Runs:** unattended
**Files:** dashboard/src/components/RolePicker.tsx (new), dashboard/src/pages/PreferencesPage.tsx (modify), dashboard/src/__tests__/RolePicker.test.tsx (new), src/strelitzia/web/static (rebuild)
**What:** `RolePicker({fields, rolesByField, value, onChange})`: chips grouped by selected field, ticked when in `value`, and a
text box that adds a custom role on Enter. Custom roles show as removable chips. Rebuild the bundle.
**Validate:** `cd dashboard && npm test -- --run && npm run build` → passes.
**Status:** ❌

## Validation
- `scripts/validate.sh --full` passes.

## Human test steps (preprod)
1. Preferences: pick "Software engineering". Suggested roles appear; tick two, add one custom role, save, reload: all three are kept.
2. Check that the What the AI judges, Companies to skip, Education and Experience rows are gone.

## Decisions
1. Grouping follows #46: Job field and Target roles sit together in Basics; Role and Eligibility merge into Role requirements.
