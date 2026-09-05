# Compass design reference

The supplied `Downloads/app 4` prototype is the visual reference, especially its
Home, SearchSetup, Results, and StyleGuide screens. Preserve its page structure,
not just its colors.

- Canvas `#FBFAF6`, white surfaces, lines `#E7E4DA`, ink `#26251F`, accent `#33615A`.
- Figtree for body and controls; Plus Jakarta Sans for headings. Fonts and their
  OFL licenses are bundled in `public/fonts` so local use has no font network dependency.
- Search starts with one prompt composer. Criteria review is a centered 672px
  page with one white sheet, editable chips, and optional preferences always visible
  below a divider. Skills, credentials, and labeled nice-to-haves share one filter section. Minimum
  experience uses a year stepper; alternate names are preserved without exposing
  separate editing controls.
- All criteria pills and add controls use a consistent 40px height. Keep add
  inputs aligned to the right of their section labels. Locations precede the
  minimum-experience stepper; target titles and industries are optional.
- Search setup belongs to the role brief. Find candidates has one explicit Run
  search action above the results, without another keywords/location form. Search
  terms come from the brief's role titles, required filters, and positive keywords;
  nice-to-haves supply the query when no primary terms are present. There is no
  separate keyword override. Network and company filters are stored
  per brief version in this browser; location comes from the role brief.
- Locations use repeatable chips and match any listed place. The existing location
  field stores alternatives separated by semicolons, preserving commas in place
  names. Queue one search per location; report any locations that could not queue.
  Industries belongs to Optional preferences.
- Find candidates keeps Cards as its default and offers Ranked list alongside it
  after candidate-list review. The list shows rank, name, score, confidence and
  profile actions. Use the existing score order; keep unscored profiles at the end.
  Search and name filters apply to both views. On phones, list rows stack their
  score and confidence below the candidate name without widening the page.
- Results use two columns of equal-width cards, a role summary above them, and
  Review/Compare actions at the bottom of each card. Cards become one column on phones.
- Candidate review opens in a 760px right-side modal drawer, preserving the
  originating results and scroll position. Use the prototype’s compact profile
  header, then show the match score and criteria breakdown immediately. Follow
  with profile actions, Review against your criteria, career history, and downloads.
  Open a passage inline, with highlighted source text and its check directly below.
  Review score evidence scrolls to the visible criteria. Keep full raw text, parsed
  fields, and history in All saved text & score history. The list check stays on
  Find candidates and opens Ranked list; the source audit is optional and separate.
  Evidence cards use thin neutral borders, without colored side rails. Verdicts
  use softly tinted text badges: teal for matches, amber for no exact/partial
  matches, neutral for unchecked evidence, and rust for conflicts. Do not add
  tick/circle glyphs or dotted underlines to verdict labels.
  The profile header scrolls with the drawer so long headlines cannot cover its
  actions; the close button stays fixed. Long text wraps without widening the
  page, while the signal table keeps its own horizontal scrolling on phones.
- On result cards, show evidence and questions before the scoring disclosure.
  In the review drawer, show the score and signal summary first. Keep unknown evidence
  distinct from an exact-text mismatch. Never label a text match as independent verification.
- Select two or three people before explicitly opening comparison; selecting a
  checkbox must not insert a table above the cards and move the user's target.
- Keep mock prototype features out of the live controls unless backed by an API.
  Descriptions are not automatically parsed into criteria. Existing criteria and
  aliases survive going back to edit the description.
- Desktop navigation stays fixed while the main content scrolls. Saved-search
  cards show no more than three profiles and use Open results for the full pool.
- Settings follows the centered Role brief layout: a 768px page, white rounded
  sheets, quiet separators, and 40px controls aligned to the right of their labels.
  Group Downloads, Search batches, and Retry timing in one form. Scoring weights
  use a separate sheet and save action, with signal labels left and inputs right.
  Keep search criteria in Role brief. Place Settings before How it works in navigation.
- The How it works guide follows the working pages' labels and action order.
  Workflow diagrams use one vertical sequence at every viewport width, with full
  explanations beside numbered stages and one connected down arrow between stages.
  Network exchanges belong within the connector stage. Navigation actions use the
  shared outline arrow icons. Its fictional exercises remain isolated from real data. Documentation screenshots
  are generated from the actual components; see [capture instructions](../docs/screenshots/README.md).

Implementation: `search-setup.css` owns the prompt/criteria screens; `results.css`
owns result headers, cards, comparison controls, and discovery layout. `compass.css`
contains the shared palette and application shell. `controls.css` gives native
selects a shared inset chevron and reserves right padding for it. `candidate-profile.css` owns
the profile drawer. Legacy evidence and diagnostic
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

User preference: controls must not gain focus rings, glow, or a colored chip
border on focus. Preserve the caret and focus behavior without adding a halo.

Expandable sections use the base Compass down/up chevron, muted regular text,
and an 8px icon-to-label gap. Hide native triangle markers; preserve native
details/summary interaction and the always-visible optional brief preferences.
