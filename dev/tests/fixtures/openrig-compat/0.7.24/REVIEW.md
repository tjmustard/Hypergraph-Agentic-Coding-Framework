# REVIEW: @openrig/cli 0.7.24 against the 0.7.23 baseline

Two runs of `dev/openrig_compat.sh 0.7.24 --harness agy`, both `openrig-compat 0.7.24: REVIEW`
(exit 3), all 16 steps ok:

1. The watcher's run, the one the person accepted on:
   `<private evidence dir>/openrig-compat-0.7.24-20261127T143203Z/`.
2. A run from a clean clone at `7a37e11`, whose `baseline-candidate/` this directory is:
   `<private evidence dir>/openrig-compat-0.7.24-20261127T162321Z/` (`verdict.json`, `REPORT.md`,
   `diffs/watched-scaffold-placeholder.js-vs-0.7.23.diff`).

Match: `diff -r` of the two `baseline-candidate/` trees (41 files each) differs only in `meta.json`,
and there only in `date` and `hacf_tree`. Verdict, baselines, watched diff, harness, help diff and
integrity are those of the watcher's run.

- Baselines: newest 0.7.23, newest person-accepted 0.7.23 (the same dir).
- Harness agy: `ok` (diff empty, 4 files).
- Help diff: none.

Only `scaffold-placeholder.js` of the watched items differs.

## scaffold-placeholder.js

```diff
--- baseline 0.7.23/scaffold-placeholder.js
+++ candidate/scaffold-placeholder.js
@@ -37,8 +37,8 @@
 export function hasAuthoredNumberedItem(body) {
     if (body === null)
         return false;
-    for (const line of body.split("\n")) {
-        const m = line.match(/^\s*\d+[.)]\s+(.+)$/);
+    for (const line of body.split(/\r?\n/)) {
+        const m = line.match(/^\s*\d+[.)]\s+(\S.*)$/);
         if (m && !isScaffoldPlaceholderText(m[1].trim()))
             return true;
     }
```

What changed, in `hasAuthoredNumberedItem`: the body is split on `\r?\n` instead of `\n`, and a
numbered item needs a non-blank character after its marker (`(\S.*)` instead of `(.+)`).

Why HACF is unaffected. Read: the diff, both versions of `scaffold-placeholder.js`, and in
`.agents/scripts/hyper_spec.py` `NUMBERED_ITEM_RE`, `_has_authored`, `_read`, `_lines`,
`_section_items` and the `mini_requirements_missing_or_malformed` call; `dev/openrig_compat.py`'s
watched list.
1. HACF mirrors rig's rule with the old regex `^\s*\d+[.)]\s+(.+)$`, but its Mini-requirements call
   passes `empty_counts=False`, so an item whose stripped text is blank is not authored.
2. Blank items: on `1.` followed by two or more spaces, 0.7.23 said authored and 0.7.24 says not
   authored, which is HACF's answer.
3. Windows line endings: HACF reads the file exactly (no newline translation), splits on LF, and
   `_section_items` strips the trailing CR, so no CR reaches its regex. On an authored item ending
   in CRLF, 0.7.23 said not authored and 0.7.24 says authored, which is HACF's answer.
4. Run, not only read: both rig versions of `hasAuthoredNumberedItem` and HACF's `_section_items`
   plus `_has_authored` on eleven item strings (authored, blank, placeholder, LF and CRLF). 0.7.24
   and HACF agreed on all eleven; 0.7.23 differed from HACF on four.
5. The behaviour captures, the scope-audit ones (`03`, `08`, `15`, `18`) among them, did not differ
   from 0.7.23's (the run reported no capture difference).
6. No HACF file outside `missions/` and the baselines calls `hasAuthoredNumberedItem`;
   `dev/openrig_compat.py` only watches the file (read, by search).

Accepted by the person: TJ's answer "Accept (Recommended)" (advisor row 450b7190, 2026-11-27), on the watcher's REVIEW of 0.7.24.

reviewed_by: build-execute@hacf (OPR.99.0.16.15 execute; the audit re-reads it)
date: 2026-11-27
