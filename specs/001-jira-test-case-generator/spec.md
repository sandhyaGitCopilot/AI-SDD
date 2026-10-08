# Feature Specification: Jira Test Case Generator

**Feature Branch**: `001-jira-test-case-generator`

**Created**: 2026-10-06

**Status**: Draft

**Input**: User description: "Build an automated test case generator that integrates with Jira via REST API. Key Requirements: 1. Requirement Ingestion: Fetch the description and acceptance criteria of a given Jira Issue Key (e.g., PROJ-123) using Jira Cloud REST API. 2. Test Generation: Generate structured manual test cases (Summary, Preconditions, Steps, Expected Results) covering edge cases and negative scenarios. 3. Human-in-the-Loop Review: Save draft test cases and halt execution until the user manually approves or edits the file in the CLI interface. 4. Output Action: Once approved, create the test cases in Jira as linked issue items using the REST API."

## Clarifications

### Session 2026-10-06

- Q: When the tool turns a Jira requirement into test cases, may the requirement text leave the user's machine and be sent to an external AI service? → A: Yes — an external AI generation service is used, with no in-tool consent step; users are assumed to have organizational approval already.
- Q: When the tool pauses for review, does the program keep running and wait at a prompt, or does it exit and let the user come back later with a second command? → A: Both — it waits at an interactive prompt by default, and the draft plus run state survive so a separate approval command can finish an abandoned run later.
- Q: After the engineer has edited a draft, what makes a test case "the same one" as before, so re-running a failed publish doesn't create duplicates? → A: Each test case carries a stable identifier assigned at generation time; the engineer is told not to change it, and cases they add get a new one.
- Q: What format should the draft file be in, given that engineers edit it by hand and the tool must read their edits back reliably? → A: CSV — a table with one row per test case, editable in a spreadsheet or a text editor.
- Q: At most how many test cases should a single run generate before the tool stops and says it has hit its limit? → A: 25 test cases per run.

### Session 2026-10-07

- Q: Should the tool load credentials from a `.env` file in the working directory, in addition to reading them from the environment? → A: Yes — load `.env` from the working directory if present, with real environment variables taking precedence over file values. The file is read only, never written to or logged, and its values go through the same redaction path as any other credential. A `.env.example` with placeholder values ships with the project.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Draft test cases from a Jira requirement (Priority: P1)

A QA engineer has a user story in Jira (for example `PROJ-123`) that is ready for test design. They run the generator with that issue key. The tool retrieves the story's description and acceptance criteria, derives a set of structured manual test cases, and writes them to a reviewable draft file on the engineer's machine. The engineer opens the draft and sees, for each test case, a summary, preconditions, numbered steps, and expected results — including cases for edge conditions and negative scenarios, not just the happy path.

**Why this priority**: This is the core value of the feature — turning a requirement into reviewable test coverage. Even if nothing is ever written back to Jira, a QA engineer who receives a solid draft has already saved the majority of manual test-design effort, so this slice is independently shippable.

**Independent Test**: Run the generator against a Jira issue that has a description and acceptance criteria, then confirm a draft file exists containing well-formed test cases that trace back to the source requirement. No write access to Jira is needed to verify this.

**Acceptance Scenarios**:

1. **Given** a Jira issue key that exists and is readable by the configured account, **When** the engineer runs the generator for that key, **Then** a draft file is produced containing at least one test case with a non-empty summary, preconditions, at least one step, and an expected result.
2. **Given** a Jira issue whose acceptance criteria describe multiple distinct conditions, **When** test cases are generated, **Then** every acceptance criterion is covered by at least one test case, and the draft records which criterion each test case traces to.
3. **Given** a Jira issue describing input validation or permission rules, **When** test cases are generated, **Then** the draft includes negative and edge-case test cases (invalid input, boundary values, unauthorized access) in addition to positive-path cases.
4. **Given** a Jira issue key that does not exist or that the configured account cannot read, **When** the engineer runs the generator, **Then** the run stops with a clear message naming the issue key and the reason, and no draft file is created.
5. **Given** a Jira issue with an empty description and no acceptance criteria, **When** the engineer runs the generator, **Then** the run stops with a message explaining that there is insufficient requirement content to generate test cases from.

---

### User Story 2 - Review, edit, and approve before anything reaches Jira (Priority: P1)

After the draft is written, the tool pauses and does not touch Jira. The engineer reviews the draft in their editor: they can correct wording, delete test cases that are not useful, add cases the generator missed, and reorder steps. They then approve the draft from the command line. Nothing is created in Jira until that explicit approval happens. If the engineer rejects the draft, or abandons the review, Jira is left untouched.

**Why this priority**: This is a safety gate, not a convenience. Without it, generated content could irreversibly pollute a shared Jira project. It is P1 alongside story 1 because the feature is not responsibly usable without it.

**Independent Test**: Run the generator, confirm the tool halts and reports where the draft is, confirm no Jira issues were created while it waits, then reject the draft and confirm Jira is still untouched.

**Acceptance Scenarios**:

1. **Given** a draft has just been generated, **When** the engineer inspects Jira, **Then** no test case issues have been created and the source issue has no new links.
2. **Given** the tool is waiting for review, **When** the engineer edits the draft file and then approves it, **Then** the approved content — including the engineer's edits — is what gets published, not the originally generated content.
3. **Given** the tool is waiting for review, **When** the engineer rejects the draft, **Then** the run ends without creating anything in Jira and the draft is retained for later reuse.
4. **Given** the engineer's edits leave the draft in a form the tool cannot interpret (a required field removed, structure broken), **When** they approve it, **Then** publishing does not start, the specific problems are reported with their locations in the draft, and the engineer can fix and re-approve.
5. **Given** the engineer marks only a subset of the drafted test cases as approved, **When** publishing runs, **Then** only the approved subset is created in Jira.
6. **Given** the engineer abandons the review prompt and closes the terminal, **When** they run the separate approval command in a later session and name that draft, **Then** the draft is published without re-reading the Jira requirement or regenerating any test case.

---

### User Story 3 - Publish approved test cases as linked Jira issues (Priority: P2)

Once the engineer approves the draft, the tool creates one Jira issue per approved test case in the target project, fills in the summary and the structured body (preconditions, steps, expected results), and links each new issue back to the source requirement so the relationship is visible from both sides. The engineer receives a summary listing each created issue key and a direct link to it.

**Why this priority**: This closes the loop and removes manual copy-paste, but the preceding stories already deliver standalone value, and publishing depends on them.

**Independent Test**: Approve a known draft against a test Jira project, then confirm each approved test case exists as an issue with the expected content and is linked to the source issue.

**Acceptance Scenarios**:

1. **Given** an approved draft with N test cases, **When** publishing completes successfully, **Then** N issues exist in the target project, each linked to the source requirement, and the engineer is shown all N issue keys.
2. **Given** publishing has created some issues and then fails partway through (connection loss, permission error, rejected field), **When** the engineer re-runs publishing for the same approved draft, **Then** already-created test cases are not duplicated and only the outstanding ones are created.
3. **Given** a draft that has already been fully published, **When** the engineer attempts to publish it again, **Then** the tool reports that it is already published and creates nothing new.
4. **Given** the configured account lacks permission to create issues in the target project, **When** publishing is attempted, **Then** the run fails before creating anything, and the message names the project and the missing permission.

---

### Edge Cases

- **Requirement content is thin**: description present but no acceptance criteria, or criteria written as free prose rather than a list — the tool still generates cases and flags in the draft that criteria coverage could not be traced precisely.
- **Acceptance criteria live outside the description**: the source issue keeps them in a separate named field, a linked child issue, or an external page. Only content the tool can read from the configured sources is used, and the draft records what was read.
- **Rich content in the requirement**: tables, images, attachments, code blocks, or embedded links. Text-equivalent content is used; non-textual content is noted as unread rather than silently dropped.
- **Very large requirement**: a description with more testable conditions than 25 test cases can cover — the tool stops at the cap, states it in the draft, and names the criteria left uncovered so the engineer can split the requirement or run again against a narrower one.
- **Duplicate work**: the source issue already has test cases linked from a previous run. The tool surfaces those before generating, so the engineer can decide whether to proceed.
- **Concurrent edits**: the source issue changes after the draft was generated but before approval — the draft records the source content it was based on, so the engineer can see it may be stale.
- **Interrupted review**: the engineer closes the terminal or the machine restarts while the tool is waiting. The draft survives and review can resume later without regenerating.
- **Approval of an empty selection**: every test case has been deleted or marked rejected — the tool reports that there is nothing to publish and exits without error.
- **Jira throttles requests**: the service rate-limits reads or writes. The tool waits and retries within bounds rather than failing the whole run, and reports if it ultimately gives up.
- **Credentials invalid or expired**: the run stops immediately with an authentication message, and does not misreport this as a problem with the issue content.
- **A `.env` file exists but cannot be used**: unreadable, or holding lines that are not `NAME=value`. The tool reports which file and which line it could not interpret and stops, rather than continuing as though the file were absent — a silently ignored credential file produces a "variable not set" error while the user is looking at the variable, which is the most confusing failure available here. A **missing** `.env` is not an error, and a line the tool skips by design (blank or a `#` comment) is not reported.
- **The same credential is set in both the environment and `.env`**: the environment value is used, and the tool is explicit that the file value was not, so a stale file cannot silently override an export the user just made.
- **Target project differs from source project**: test cases are filed in a dedicated QA project while the requirement lives elsewhere — the link must still be established across projects.
- **A spreadsheet application rewrites the draft on save**: changed character encoding, a locale-specific column separator, quotes or line breaks inside cells re-escaped, or an identifier reformatted as a number or date. The tool reads the draft back defensively and reports what it could not interpret by row and column rather than publishing mangled content.
- **Multi-line content inside a cell**: steps and expected results contain line breaks, commas, and quote characters that CSV must escape. Round-tripping a draft through an edit must not lose step order or merge steps together.
- **Test case identifiers are damaged by editing**: the engineer copies a case and leaves the identifier duplicated, or deletes an identifier that was already published. A duplicate blocks approval with the locations reported; a missing already-published identifier produces a warning rather than a silent second issue.
- **Generation service fails or returns unusable output**: unreachable, rejects the request, hits its own size or quota limits, or returns content that is not interpretable as test cases. The run stops with a message distinguishing this from a Jira problem, and no partial draft is left behind.

## Requirements *(mandatory)*

### Functional Requirements

#### Requirement ingestion

- **FR-001**: Users MUST be able to start a run by supplying a single Jira issue key (for example `PROJ-123`).
- **FR-002**: System MUST retrieve the source issue's summary, description, and acceptance criteria content, and MUST record in the draft exactly which fields were read.
- **FR-003**: System MUST validate the supplied issue key before doing any generation work, and MUST stop with a distinct, actionable message for each of: malformed key, issue not found, no permission to read the issue, and authentication failure.
- **FR-004**: System MUST stop with an explanatory message, and produce no draft, when the retrieved requirement content is empty or too sparse to generate test cases from.
- **FR-005**: System MUST report any test cases already linked to the source issue before generating new ones.

#### Test case generation

- **FR-006**: System MUST generate manual test cases in which each case has a summary, preconditions, an ordered list of steps, and expected results.
- **FR-006a**: System MUST assign every generated test case a stable identifier, unique within the draft, and MUST record it in the draft alongside a note that it should not be edited. This identifier — not the summary text and not the case's position — is what identifies a test case for the rest of its life.
- **FR-007**: System MUST cover every identified acceptance criterion with at least one test case, and MUST record the traceability from each test case back to the criterion or requirement text that motivated it.
- **FR-008**: System MUST include negative scenarios (invalid input, missing data, unauthorized or out-of-sequence actions) and edge cases (boundary values, empty and maximum-size inputs, repeated or interrupted actions) in addition to positive-path cases.
- **FR-009**: System MUST state in the draft when coverage is incomplete, naming what was not covered and why (sparse criteria, non-textual content, or a generation cap being reached).
- **FR-010**: System MUST produce test cases that are self-contained — readable and executable by a tester who has not read the source Jira issue.
- **FR-011**: System MUST limit a single run to 25 generated test cases. When the limit is reached, the draft MUST say so and MUST name the acceptance criteria or requirement areas that were left uncovered as a result.

#### Human-in-the-loop review

- **FR-012**: System MUST write the generated test cases to a draft file at a stable, reported location before any write to Jira is attempted.
- **FR-012a**: The draft MUST be a CSV table with one row per test case and a header row, carrying at minimum a column each for the test case identifier, summary, preconditions, steps, expected results, traced criteria, and approval state. It MUST open correctly in a spreadsheet application and remain editable in a plain text editor.
- **FR-012b**: System MUST represent multi-step content — steps and expected results — within a single cell in a consistent, documented way, so that a tester reading the row can follow the steps in order and the tool can recover that order after the user has edited the cell.
- **FR-013**: System MUST halt after writing the draft — waiting at an interactive prompt by default — and MUST NOT create, modify, or link anything in Jira until the user explicitly approves.
- **FR-014**: Users MUST be able to edit the draft — change text, delete rows, add rows, reorder steps within a cell — in either a spreadsheet application or a text editor, and have those edits be what gets published.
- **FR-015**: Users MUST be able to approve, reject, or approve a subset of the drafted test cases, either at the interactive prompt or through a separate approval command invoked later.
- **FR-016**: System MUST validate an approved draft before publishing, and when the draft cannot be interpreted MUST report each problem by row number and column name, and allow the user to fix and re-approve without regenerating. Validation MUST detect a missing or renamed required column, a row missing a required value, and a row whose step and expected-result content cannot be read back. It MUST reject a draft containing a duplicated test case identifier, and MUST warn when an identifier recorded as already published has gone missing from the draft.
- **FR-017**: System MUST persist the draft and enough run state that a review interrupted in any way — prompt abandoned, terminal closed, machine restarted — can be completed later by the separate approval command, publishing without re-fetching the requirement or regenerating test cases.
- **FR-017a**: Users MUST be able to list the drafts still awaiting review and name which one to approve, so that several outstanding runs are never ambiguous.
- **FR-018**: System MUST report that there is nothing to publish, and exit without error, when an approved draft contains no approved test cases.

#### Publishing to Jira

- **FR-019**: System MUST create one Jira issue per approved test case, populating the summary and a body containing preconditions, steps, and expected results in a consistent, readable layout.
- **FR-020**: System MUST link each created issue to the source requirement issue so the relationship is visible from both the requirement and the test case.
- **FR-021**: System MUST support creating test cases in a target project that differs from the source issue's project.
- **FR-022**: System MUST verify that the configured account can create issues and links in the target project before creating anything, and MUST stop with a message naming the project and the missing permission when it cannot.
- **FR-023**: System MUST record, against each test case identifier, the Jira issue created for it, so that re-running publication for the same approved draft creates only the test cases whose identifiers have no recorded issue, and never duplicates. Rewording a summary or reordering the draft MUST NOT cause a second issue to be created for an identifier that already has one.
- **FR-023a**: Users MUST be able to add a test case to a draft by hand; a case with no identifier is treated as new, assigned one during approval validation, and published alongside the generated cases.
- **FR-024**: System MUST report, at the end of a publish, every created issue key with a direct link, plus every test case that failed and the reason.
- **FR-025**: System MUST tolerate transient failures (rate limiting, timeouts) by retrying within a bounded number of attempts, and MUST report clearly when it stops retrying.

#### Operation and configuration

- **FR-026**: Users MUST be able to configure the Jira site, credentials, target project, and the issue type and link relationship used for test cases, without editing program internals.
- **FR-026a**: System MUST read credentials from environment variables, and MUST additionally read them from a `.env` file in the working directory when one is present. A value already set in the environment MUST take precedence over the same name in the file, so an explicit export is never silently overridden by a stale file. A missing `.env` file MUST NOT be an error.
- **FR-026b**: System MUST treat a value loaded from `.env` exactly as it treats one from the environment — same redaction, same refusal to write it to any run artifact (FR-027). System MUST NOT create, modify, or write to `.env`, and MUST NOT record its contents or its resolved path's values in logs or error messages. The project MUST ship a `.env.example` holding placeholder values only, and MUST exclude `.env` from version control.
- **FR-026c**: When a `.env` file is present but cannot be interpreted, System MUST stop and report the file and the offending line number, rather than proceeding as though the file were absent. Blank lines and lines beginning `#` MUST be skipped without comment. An error message about `.env` MUST NOT quote the line's value, since that value is the secret.
- **FR-027**: System MUST never write credentials into the draft file, logs, or terminal output.
- **FR-028**: System MUST indicate success only when the requested work actually completed, and indicate failure distinctly otherwise, so it can be used in scripted workflows.

#### Generation service and data handling

- **FR-029**: System MUST generate test cases using an external AI generation service, transmitting the retrieved requirement content to that service. The tool MUST NOT require or prompt for per-run user consent; organizational approval for this processing is a precondition of use rather than something the tool verifies.
- **FR-030**: System MUST state in the draft which generation service produced the test cases and when, so a reviewer can tell what processed the requirement.
- **FR-031**: System MUST stop with a clear, distinct message — separate from Jira errors — when the generation service is unreachable, rejects the request, exceeds its own limits, or returns output that cannot be read as test cases, and MUST NOT write a partial draft in those cases.

### Key Entities

- **Source Requirement**: the Jira issue identified by the supplied key. Carries the key, summary, description text, acceptance criteria content, and the time it was read.
- **Acceptance Criterion**: a single discrete condition extracted from the source requirement; the unit coverage is measured against.
- **Test Case**: one manual test — a stable identifier, summary, preconditions, ordered steps, expected results, the criteria it traces to, and its own approval state. The identifier is assigned at generation, is unique within the draft, and is the only thing that identifies the case across edits and publish attempts.
- **Draft**: the reviewable artifact holding a run's test cases plus the source content they were generated from, the coverage notes, and anything the tool could not read.
- **Approval Decision**: the user's verdict on a draft — approved, rejected, or approved in part — together with which test cases it applies to.
- **Publication Record**: the mapping from each test case identifier to the Jira issue created for it, used to make re-runs safe and to report results.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A QA engineer can go from a Jira issue key to a reviewable draft of test cases in under 2 minutes for a typical user story.
- **SC-002**: Producing and filing a full set of test cases for one requirement takes under 10 minutes of the engineer's time, against a baseline of 45–60 minutes of manual authoring and data entry.
- **SC-003**: Across a sample of reviewed drafts, at least 80% of generated test cases are published with no edits or with wording edits only.
- **SC-004**: Every acceptance criterion in the source requirement is covered by at least one generated test case in 100% of runs where criteria are present and identifiable, or the shortfall is explicitly disclosed in the draft.
- **SC-005**: At least 30% of generated test cases per run address negative or edge-case scenarios rather than the positive path.
- **SC-006**: In 100% of runs that are rejected or abandoned at review, Jira contains no new issues and no new links.
- **SC-007**: No run ever produces duplicate test case issues in Jira for the same approved test case, including after an interrupted publish is re-run.
- **SC-008**: Every failure a user can hit — bad key, no permission, bad credentials, empty requirement, unparseable draft, publish failure — produces a message naming what went wrong and what to do next, verified by walking each case.
- **SC-009**: A tester who has not read the source Jira issue can execute a published test case without asking for clarification, for at least 90% of published cases.
- **SC-010**: An engineer interrupted during review can resume and publish in a later session without regenerating, in 100% of cases.
- **SC-011**: A draft at the maximum size of 25 test cases can be reviewed and approved within the 10-minute budget in SC-002.
- **SC-012**: A draft opened in a spreadsheet application, edited, and saved from it is still accepted for publishing, with step order and identifiers intact, in 100% of cases.

## Assumptions

- **Target users**: QA engineers, test leads, and developers who already work in Jira daily, are comfortable in a terminal, and hold a Jira Cloud account with read access to the requirements they query.
- **One requirement per run**: a run handles a single issue key. Bulk generation across an epic, sprint, or saved filter is out of scope for the first version.
- **Manual test cases only**: the output is human-executable test cases. Generating automated test scripts is out of scope.
- **Native Jira issues, not a test management add-on**: test cases are filed as ordinary Jira issues linked to the requirement, as the request states. Add-on-specific test objects are out of scope, though a configurable issue type leaves room for a site that models tests as a custom issue type.
- **Issue type and link relationship are configurable**, defaulting to a standard task-style issue type and a non-directional "relates to" link, because these vary by Jira site.
- **Acceptance criteria location is configurable**: criteria are read from the description by default, with an option to name a dedicated field, because Jira sites differ in where they keep them.
- **Review happens in the user's own editor or spreadsheet application**: the tool writes the draft, pauses, and takes approval at the command line. It does not provide its own editing surface.
- **The draft is a CSV table**, one row per test case, so it can be reviewed in a spreadsheet by testers who do not work in a text editor and interoperates with existing CSV-based test case tooling. The accepted tradeoff is that multi-step content lives inside single cells and that spreadsheet applications may rewrite the file on save, which FR-012b and FR-016 require the tool to handle defensively rather than assume away.
- **Credentials come from the environment, or from a `.env` file in the working directory that the user controls**, consistent with standard practice for command-line tools, and are never stored in run artifacts. The environment wins where both supply the same name. The accepted tradeoff is that a `.env` file puts a token in plaintext on disk: it is excluded from version control and never written to by the tool, but a user who copies the directory, bakes it into a container image, or force-adds it to a commit can still leak it. The alternative — exporting the token in a shell, where it lands in shell history — is also plaintext and was judged no safer.
- **A single user per run**: no multi-user approval workflow, review assignment, or audit trail beyond the local run artifacts.
- **Network access is available** to the Jira site at the ingestion and publishing steps, and to the external generation service at the generation step; offline operation is out of scope.
- **Organizational approval for external AI processing is already in place.** Requirement content is sent to an external generation service, and the tool treats permission to do so as a precondition of use: it does not prompt for consent, gate on a policy check, or redact content before sending. Teams whose requirements may not leave their environment are not served by this version. Enforcing such a policy — redaction, a local generation engine, or an approval gate — would be a separate feature.
- **English-language requirement content** is assumed for the first version.
- **Existing Jira projects and permissions are reused**: the feature does not create projects, issue types, or link types, and does not grant permissions.
