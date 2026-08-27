# Site-Specific Passwords

## Problem

The `applypilot init` wizard asks for a single "job site password" (`src/applypilot/wizard/init.py:181`), but users have different passwords for different ATS platforms (Workday, Greenhouse, Lever, etc.). A single password doesn't work when the agent encounters login walls on different employer career sites.

## Goal

Replace the single `personal.password` field with a `site_passwords` dict that maps ATS platform names to credentials. The wizard prompts for passwords per-ATS, and the auto-apply agent uses the correct password for each site.

## Design

### New profile.json schema

Replace `personal.password` with a top-level `site_passwords` dict:

```json
{
  "personal": {
    "...existing fields...",
    "password": ""  // deprecated, kept for backward compat
  },
  "site_passwords": {
    "workday": "my_workday_pass",
    "greenhouse": "my_greenhouse_pass",
    "lever": "",
    "ashby": ""
  }
}
```

### Supported ATS platforms

Define a canonical registry in `src/applypilot/config.py`:

| ATS | Login domain pattern | Account signup URL |
|---|---|---|
| Workday | `*.myworkdayjobs.com` | (varies by employer) |
| Greenhouse | `boards.greenhouse.io` | (varies by employer) |
| Lever | `jobs.lever.co` | (varies by employer) |
| Ashby | `jobs.ashbyhq.com` | (varies by employer) |

These match the ATS detection already in `src/applypilot/enrichment/detail.py:288-290` (Ashby, Greenhouse selectors) and the Workday employer registry (`config/employers.yaml`).

### Wizard flow change

In `src/applypilot/wizard/init.py`, `_setup_profile()`:

1. **Remove** the single `password` prompt at line 181
2. **Add** a new `site_passwords` section after the existing personal info prompts
3. For each supported ATS, prompt: `"<ATS> password (leave blank if you don't apply to sites on this platform)"`
4. Store the result as `profile["site_passwords"] = {"workday": "...", "greenhouse": "...", ...}`

The prompt should show a brief description of each platform so the user knows what it is:
- "Workday (used by TD, CIBC, RBC, BMO, NVIDIA, Netflix, etc.)"
- "Greenhouse (used by many tech startups)"
- "Lever (used by many tech companies)"
- "Ashby (used by many startups)"

### Prompt builder change

In `src/applypilot/apply/prompt.py`, `build_prompt()`:

1. Load `site_passwords` from the profile (with fallback to `personal.password` for backward compat)
2. Build a password lookup table in the prompt
3. Replace lines 572-574 with site-aware instructions:

**Current (line 572):**
```
5c. Regular login form (employer's own site)? Try sign in: {email} / {password}
```

**New:**
```
5c. Regular login form? Identify the ATS from the URL, then use the matching credentials:

  | ATS | URL pattern | Email | Password |
  |-----|-------------|-------|----------|
  | Workday | *.myworkdayjobs.com | {email} | {workday_password} |
  | Greenhouse | boards.greenhouse.io | {email} | {greenhouse_password} |
  | Lever | jobs.lever.co | {email} | {lever_password} |
  | Ashby | jobs.ashbyhq.com | {email} | {ashby_password} |

  If no password configured for this ATS (blank), try sign-in anyway in case no password is required.
  If sign-in fails and no password configured, output RESULT:FAILED:no_password_configured.
```

### Backward compatibility

In `config.py:load_profile()`, add migration logic:
- If `site_passwords` key is missing and `personal.password` is non-empty, migrate it to `site_passwords.workday` (most common use case)
- Write the migrated profile back to disk

### Example file update

Update `profile.example.json` to show the new schema with `site_passwords` and deprecation note on `personal.password`.

## Files to modify

| File | Change |
|---|---|
| `src/applypilot/config.py` | Add `SITE_PASSWORDS` registry dict with ATS names/domains/descriptions. Add backward-compat migration in `load_profile()`. |
| `src/applypilot/wizard/init.py` | Remove line 181 (`personal.password` prompt). Add `_setup_site_passwords()` function that prompts per-ATS. Call it from `_setup_profile()`. |
| `src/applypilot/apply/prompt.py` | Load `site_passwords` from profile. Build password lookup table. Replace lines 572-574 with site-aware login instructions. |
| `profile.example.json` | Add `site_passwords` section. Mark `personal.password` as deprecated. |
| `tests/test_init_wizard.py` | Add tests for the new site passwords wizard step. |

## Acceptance criteria

1. `applypilot init` prompts for passwords per-ATS (Workday, Greenhouse, Lever, Ashby) with descriptive labels
2. Passwords are stored under `site_passwords` in `profile.json`
3. Old profiles with `personal.password` are migrated automatically on load
4. The auto-apply prompt includes a site-specific password lookup table
5. The agent knows to identify the ATS from the URL and use the matching password
6. All existing tests pass
7. New tests cover the wizard password prompts and prompt builder changes
