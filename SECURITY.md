# Security policy

cas-portfolio handles personal financial data, so reports about how it stores, exposes or deletes that data matter most. Examples:

- a way for another website or another device to read or change data in a running instance
- personal details (PAN, full account or folio numbers, names) being stored, logged or exported
- backups, exports or the demo mode exposing real data
- the statement PDF or its password being written to disk or logged

The app has no login of its own and is meant to listen on `127.0.0.1` only (see the README). Exposing it to the internet without authentication in front of it is not a supported setup.

## Reporting a problem

Use GitHub's private reporting: open the **Security** tab of this repository and choose **Report a vulnerability**. Please do not open a public issue with details.

**Never attach a statement PDF, a database or a backup, and never paste unredacted output.** Describe the problem with made-up numbers.

## Supported versions

Only the latest commit on `main` gets fixes. This is a personal project, so there is no fixed response time, but reports about data exposure are handled first.
