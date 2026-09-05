# Making candidate review understandable

## User questions and the corresponding actions

| User question | Place | Action and result |
| --- | --- | --- |
| Are these the people I meant to find? | Find candidates | Check names, LinkedIn links, and repeated source searches. Add a note and confirm the list. Ranking opens on the same page. |
| Why might this person fit? | Candidate drawer | Read score and confidence, then Review against your criteria and Career history. A matched term is a reason to investigate the profile. |
| Does the source support this result? | Inline source under a criterion | Open a passage, read the highlighted text in context, and explicitly check it. The score stays unchanged. |
| What is still missing? | Criteria and Evidence & downloads | Distinguish no exact match from missing or unparseable data. Request relevant sections, or carry the question into a conversation. |
| Who should I compare? | Compare matches | Select two or three people and read across the same criteria. Source audit completion is not required. |
| How do I keep an audit of source checks? | Record source checks | Check at least ten distinct passages for current scores, add a note, and record them. This is separate from candidate approval. |

## Changes

Previously, “review” referred to list acceptance, candidate assessment, and source
verification with little distinction. List confirmation changed pages, while the
source viewer and verification checkbox were separated by other content.

The new flow keeps users with the results after list confirmation. The drawer
shows criteria once, with a source viewer and check directly under the selected
passage. Complete raw sections and score history remain available in a secondary
disclosure. The optional audit is discoverable above comparison results, has a
plain-language action, and starts with an empty note.

How it works mirrors the actual labels and sequence. The candidate tour includes
a hands-on PostgreSQL example: usage is supported by the passage, while architecture
responsibility and scale still need investigating. The example does not equate a
checked passage with a verified qualification or an approved candidate.

## Boundaries retained

- Ranking still requires the existing list gate. The ten-source audit requirement
  is unchanged; no backend scoring, retrieval, or persistence rules changed.
- Opening a source never checks it automatically. A source check requires matching
  candidate, profile section, evidence span, available provenance, and valid Unicode
  bounds from the loaded saved section. Failure, withholding, or a stale source
  leaves checking disabled.
- Unrecorded checks belong to the current score identity and are temporary. Reloads
  and scoring-input changes clear them. Recorded audits remain historical records.
- Scores describe retrieved evidence. Source checks do not change scores or certify
  qualifications. There is no new shortlist, hiring-decision, or messaging feature.

## Verification

91 frontend tests pass. The 17 browser scenarios pass, including the final
list-confirmation scenario rerun after completing its mocked search payload.
Build and lint pass. The browser checks cover keyboard passage highlighting,
explicit source checks, mobile layouts, and staying in Find candidates after
confirmation. README screenshots use fictional data and intercepted read-only APIs.
