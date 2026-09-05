# Compass

Find candidates on LinkedIn, review the evidence behind their scores, and compare
people against your role criteria. Searches and downloaded profiles stay on your
computer, ready to revisit.

## Get started

Download or clone this repository, open its folder in a terminal, and run:

```sh
./compass
```

Compass installs what it needs, opens the app, and opens a LinkedIn sign-in window
on first use. Sign in, complete **Search criteria**, then choose **Run search**.
Later launches reuse your installation and login.

**Requirements:** macOS or desktop Linux, Git, curl, and internet access for setup.
Linux also needs Chromium's system libraries. The first launch may take a few
minutes. Keep the terminal open; **Ctrl+C** stops Compass without deleting saved work.

[Startup help](docs/usage-notes.md#startup-help) · [Developer setup](docs/development.md)

![Compass candidate results with evidence summaries and review actions](docs/screenshots/candidate-results.png)

*Screenshots use fictional candidates in the working app.*

## From search criteria to comparison

1. **Set your search criteria.** Edit the role description, skills, credentials,
   locations, and minimum experience on one page. Add optional preferences where
   useful, then choose **Continue to search**. The description is not automatically
   converted into criteria.
2. **Find candidates.** Run a search. New profiles download and receive scores
   automatically with the default settings; existing downloads are reused.
3. **Review the list.** Check names and duplicate sources, add a review note,
   then choose **Confirm list & show ranking**. Switch between Cards and Ranked
   list, with up to 30 profiles per page.
4. **Inspect and compare.** Open **Review** to see a candidate’s score and saved
   evidence. In **Compare matches**, select two or three people and choose
   **View comparison**.
5. **Return to saved work.** **Saved searches** keeps previous runs together.
   **Open results** returns to the full list.

Use **Settings** for download limits, pacing, and scoring weights. Changing
weights recalculates saved evidence locally. The in-app **How it works** guide
walks through the same workflow with interactive examples.

## Downloads, scores, and your data

- **Batch size:** up to 1,000 new profile downloads per search batch by default.
  This is not a limit on the total profiles you can save. LinkedIn may return fewer results.
- **Speed:** one or two profiles download at a time, depending on Settings and
  connector support. A larger batch does not increase simultaneous downloads.
- **Reach:** choose first-degree, second-degree, or third-degree and beyond in
  Search criteria. Leave all unchecked to search across networks. Connection distance
  does not affect scores.
- **Profile visits:** retrieval opens LinkedIn pages in your signed-in session.
  Hidden browser tabs do not make visits anonymous.
- **Scores:** scores reflect retrieved evidence against your criteria. Confidence
  reflects evidence availability. Neither verifies a qualification or predicts
  job performance; open the saved sources to assess the match.
- **Local storage:** searches, profiles, and evidence are saved in
  `~/.linkedin-dashboard/session.db`. You can review saved work while the connector
  is offline. Compass does not send messages or connection requests.

## Screenshots

<details>
<summary>Search criteria, ranked results, candidate review, and settings</summary>

### Search criteria

![Search criteria with an editable role description, skills, locations, and minimum experience](docs/screenshots/role-brief.png)

### Ranked results

![Candidates ranked by score, with confidence and review actions](docs/screenshots/ranked-list.png)

### Candidate review

![Candidate drawer with score and signal breakdown](docs/screenshots/candidate-review.png)

### Settings

![Download controls and scoring weights](docs/screenshots/settings.png)

### Saved searches

![Saved search with a three-person preview](docs/screenshots/saved-searches.png)

### How it works

![Interactive Compass guide](docs/screenshots/how-it-works.png)

</details>

## More information

- [Settings reference](docs/settings.md) — download and scoring controls.
- [Usage notes](docs/usage-notes.md) — startup help and current limitations.
- [Developer guide](docs/development.md) — contributing and technical documentation.

## Acknowledgments

Thank you to [Daniel Sticker (@stickerdaniel)](https://github.com/stickerdaniel),
author of [linkedin-mcp-server](https://github.com/stickerdaniel/linkedin-mcp-server),
and its contributors. Their open-source connector powers Compass’s LinkedIn access.

## License

Compass is [MIT licensed](LICENSE). The LinkedIn connector retains its
[Apache-2.0 license](integrations/linkedin-mcp-server/LICENSE).
Bundled fonts retain their SIL Open Font Licenses:
[Figtree](frontend/public/fonts/figtree-OFL.txt) and
[Plus Jakarta Sans](frontend/public/fonts/plusjakartasans-OFL.txt).
