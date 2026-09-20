# Why I built it this way

The main idea is simple: **someone's private career details should belong to the
job they held, even when that job appears in several CVs.**

For example, Maya might have an English CV, a German CV, and a version tailored
for a particular application. If all three describe the same management role,
she should enter her salary once. Changing it through any of those CVs should
update what she sees through the others.

## Store the details once

Previously, private details belonged to one entry in one CV. Importing another
version created new entries, which had no connection to the original details.

I added two things for the backend to remember: the person described by a CV,
and the jobs that person held. Several CV entries can point to the same job.
That job has one set of private details.

This keeps the CVs independent: their wording, language and layout can differ.
Only the private details are shared. The existing API and CLI still work in the
same way from the user's point of view.

## Check the person before checking the job

One account can contain CVs for different people. So being in the same account,
or having worked at the same company, is not enough to share private information.

The backend first checks the person's name together with their email or date of
birth. It allows an omitted middle name but rejects conflicting identity evidence.
When there is not enough evidence, it keeps the people separate.

Only then does it compare jobs belonging to that person. It looks at the employer,
role and dates. It allows small differences, such as a company suffix or a missing
month, without treating every similar job as the same one. A promotion or a later
return to the same employer can still be a separate experience.

## Use AI for differences in wording

Straightforward matches use ordinary rules. Harder cases, such as translated or
rewritten job titles, can use the existing OpenAI integration.

The AI only chooses from jobs the backend has already selected for the same
person. It cannot select another account's job. Its answer is checked, and it can
say that there is no clear match.

The request includes job descriptions and dates, but not the separate personal
details or private salary, hours and reporting fields. Job descriptions can still
contain personal information, so this is not a complete anonymization system.

There are limits on request size, the number of calls, and waiting time. If the AI
fails or the result is uncertain, the entries stay separate. Once a match is saved,
reading the details does not require another AI call.

**I would rather miss a match than incorrectly share someone's private details.**
A missed match is inconvenient; a wrong match can expose information or attach it
to the wrong person.

## Make everyday changes behave predictably

- **Copying a CV:** keep its existing job connections. We already know where the
  copy came from, so there is no need to match it again.
- **Rewriting bullet points:** keep the connection. Changing the person, employer,
  role or dates can trigger another check.
- **Changing private details:** update the one shared record, so matching CVs see
  the same values. A later successful update replaces the earlier values.
- **Deleting a CV:** keep details that another CV still uses. Remove them when no
  CV entry refers to that job anymore.
- **Deleting private details:** clear them for all CV entries sharing that job.

The backend also prevents separate entries in one CV from claiming the same job
during matching. Adding or rearranging entries should not take an existing entry's
connection away from it.

## Keep existing data and simultaneous updates safe

The database upgrade preserves existing private values and their timestamps.
It starts by keeping old records separate. A separate reconciliation command then
checks which records can share details.

If two matching records contain different salaries or other private values, the
backend keeps them separate. It does not silently choose which value is correct.
The command can also retry matches that were previously uncertain.

Related changes are saved together: either the whole change succeeds or none of
it does. Updates within an account are coordinated so that two requests arriving
at once cannot create competing links or overwrite only part of a record.

This is a simple approach for the exercise. One trade-off is that an AI request
can make another update to the same account wait. For a larger product, I would
move that slow work outside the database transaction and check that the CV had
not changed before saving the result.

## What I checked

The solution passed **35 automated tests**, plus type and code-style checks. These
cover sharing, updates, different people and accounts, duplication, edits,
deletions, simultaneous requests, and preservation of existing data.

The automated tests use controlled AI responses. I also ran a separate live check
with OpenAI and all four supplied examples:

- Maya's matching roles shared details across the English, German and varied CVs.
- An update through the German CV appeared through the English CV.
- Mia's roles stayed separate, as did Maya's additional Harbor Systems role.

The database upgrade was applied to the supplied hosted database, and application
startup and the database health check passed there.

## What I would improve next

Matching is still a judgment based on incomplete information. Changed names,
missing dates, renamed companies, or two people with indistinguishable identity
information can cause missed or incorrect matches. AI can also make mistakes.
Passing the supplied examples does not prove accuracy on every CV.

For a real product, my next steps would be to let users confirm which person a CV
belongs to, inspect and undo a match, and test against a larger multilingual set
of both matching and non-matching examples. Large accounts would also need more
efficient searches for possible matches.

The current implementation is somewhat heavier than the smallest possible
solution, mainly because it handles old data and conflicting values. The first
code cleanup I would make is separating that reconciliation work from the normal
import and editing flow.
