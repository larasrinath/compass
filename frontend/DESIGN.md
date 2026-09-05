# Compass design reference

The supplied `Downloads/app 4` prototype is the visual reference, especially its
Home, SearchSetup, Results, and StyleGuide screens. Preserve its page structure,
not just its colors.

- Canvas `#FBFAF6`, white surfaces, lines `#E7E4DA`, ink `#26251F`, accent `#33615A`.
- Figtree for body and controls; Plus Jakarta Sans for headings. Fonts and their
  OFL licenses are bundled in `public/fonts` so local use has no font network dependency.
- Search starts with one prompt composer. Criteria review is a centered 672px
  page with one white sheet, editable chips, and secondary settings in disclosures.
- Results use two columns of equal-width cards, a role summary above them, and
  Review/Compare actions at the bottom of each card. Cards become one column on phones.
- Show evidence and questions before score calculations. Keep unknown evidence
  distinct from an exact-text mismatch. Never label a text match as independent verification.
- Select two or three people before explicitly opening comparison; selecting a
  checkbox must not insert a table above the cards and move the user's target.
- Keep mock prototype features out of the live controls unless backed by an API.
  Descriptions are not automatically parsed into criteria. Existing criteria and
  aliases survive going back to edit the description.

Implementation: `search-setup.css` owns the prompt/criteria screens; `results.css`
owns result headers, cards, comparison controls, and discovery layout. `compass.css`
contains the shared palette and application shell. Legacy evidence and diagnostic
views still have styles in `App.css`.

Typography roles are defined centrally in `index.css`: Figtree 400 for body text,
500 for navigation, quiet actions, disclosures and badges, and 600 for field labels
and primary actions. Plus Jakarta Sans 700 is for section/candidate headings;
800 is reserved for page titles and the brand. Use the supplied font weights
exactly (no 550/650/750 fallbacks). Supporting text uses `--ink-faint` (#8A8577),
body copy uses `--ink-soft` (#4A473E), and headings/field labels use `--ink` (#26251F).
Keep status labels in sentence case. Page styles should use these tokens rather
than introducing near-matching colors. Check computed browser styles as well as
screenshots whenever changing typography, since legacy selector specificity can
otherwise silently override the intended face, weight, or color.
