# Project workflow

GitHub repository: https://github.com/praveenck23009864/fiber-plans-comparison.git

After each completed and verified milestone:

1. Run the checks relevant to the changes.
2. Review the Git diff and stage only files belonging to that milestone.
3. Commit with a clear message describing the completed work.
4. Push to the current working branch in the repository above.
5. Report the milestone, test results, branch, and commit hash.

Before the first push, verify the Git repository, remote URL, and current branch.
Do not overwrite an unrelated remote.

Never commit .env files, API keys, credentials, browser profiles, venv,
node_modules, or private diagnostic captures. Exclude generated SQLite databases
unless explicitly requested. Commit schema migrations and safe sample data where
appropriate.

Do not force-push, discard unrelated changes, or push failing work.
If authentication or a push fails, preserve the local commit and report the exact
blocker.

Apply this workflow to completed, uncommitted milestones. Preserve existing
project instructions when updating this file.
