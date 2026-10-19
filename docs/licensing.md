# Licensing decision

## Goal

The owner wants an open, flexible licence that:

- Encourages contributions back to the project
- Preserves attribution to the original project
- Remains a standard open-source licence (no custom “mandatory PR” clause)

## Important distinction

| Model | What it typically requires |
|---|---|
| **Permissive** (MIT, BSD, Apache-2.0) | Keep copyright/licence notices; few other obligations. Derivatives may be closed. |
| **Weak copyleft** (LGPL) | Sharing source for modifications to the library itself under specified conditions. |
| **Strong copyleft** (GPL-3.0) | Conveying binaries generally requires corresponding source under GPL terms. |
| **Network copyleft** (AGPL-3.0) | Like GPL-3.0, plus an obligation when users interact with a modified version over a network. |

Standard open-source licences (including AGPL) **do not** require submitting changes or
pull requests to the original upstream repository. Attribution and source-sharing
obligations are not the same as “mandatory contribution to this GitHub repo.”

An upstream repository link is also **not** a substitute for providing the source that
corresponds to a modified deployed version where the licence requires it.

## Why AGPL-3.0 is a fit here

This project is a **network-accessible** management and demo stack served from RockPi
devices on a LAN (setup UI, AP portal handoff, management API). AGPL-3.0’s section 13
addresses the common “SaaS / appliance” gap in GPL-3.0: if you run a modified version
and users interact with it remotely, you must offer them corresponding source.

That aligns with:

- Keeping deployed forks inspectable by users of the setup UI
- Preserving attribution and licence notices
- Encouraging (without inventing a custom legal duty) reciprocal sharing

## Tradeoffs

**Benefits**

- Strong reciprocity for networked modifications
- Clear FSF/OSI-recognised licence text
- Compatible with community contribution norms while staying honest about legal limits

**Costs / friction**

- Some organisations avoid AGPL for compliance policy reasons
- Downstream redistributors must plan for source offer workflows
- Combining with proprietary components is harder than under permissive licences
- Contributors must be comfortable with AGPL terms for their patches

**What AGPL does not do**

- It does not force push access or PRs to https://github.com/gufmar/cardano-on-the-rocks
- It does not replace good documentation of how to obtain corresponding source
- It does not permit omitting copyright notices

## Decision

**Licence: GNU Affero General Public License v3.0 (AGPL-3.0-only)**

- Full text: [LICENSE](../LICENSE)
- Reference: https://www.gnu.org/licenses/agpl-3.0.html
- Open Source Definition: https://opensource.org/osd

## Attribution requirements for this project

1. Keep copyright and licence headers / notices intact.
2. README and any application About page must credit the original project and link to
   https://github.com/gufmar/cardano-on-the-rocks
3. Modified network-facing deployments must offer corresponding source for the running
   version (AGPL §13), not merely an upstream URL if the deployed code differs.
