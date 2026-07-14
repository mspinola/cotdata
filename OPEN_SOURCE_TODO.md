# Remaining Open Source Tasks

This checklist tracks the remaining tasks to get `cotdata` fully ready for public release and PyPI publication.

## Code & Repository Updates
- [ ] **Update `pyproject.toml` Metadata**: Add the required fields to publish to PyPI.
  - `authors` list (e.g., `[{name = "Matt S.", email = "..."}]`)
  - `urls` block (e.g., `Homepage`, `Repository`, `Issues`)
  - `classifiers` (e.g., `License :: OSI Approved :: MIT License`, `Programming Language :: Python :: 3`)
- [ ] **Update `README.md` Installation Instructions**: Add a generic `pip install cotdata` and PyPI link, moving the local `uv` workspace instructions to an "Advanced/Development" section.
- [ ] **Add Badges to `README.md`**: Add markdown shields at the top of the README for CI Status, PyPI Version, and MIT License.
- [ ] **Create Issue Templates**: Add `.github/ISSUE_TEMPLATE/bug_report.md` and `feature_request.md` (make sure they ask the user to specify their OS since Norgate requires Windows).
- [ ] **Create Pull Request Template**: Add `.github/PULL_REQUEST_TEMPLATE.md` to ensure PRs reference related issues and confirm tests have passed.

## GitHub Settings Configuration
*(To be done in the repository's Settings tab on github.com)*
- [ ] **Branch Protection Rules**: Protect `main`. Require PRs before merging and require the "test" GitHub Action status check to pass.
- [ ] **Security & Analysis**: Enable Dependabot alerts and security updates for dependencies.
- [ ] **Enable Discussions**: Turn on GitHub Discussions in settings to field general usage/data questions away from the Issue tracker.
- [ ] **Repository Details**: Add a project description, website link, and tags/topics (e.g., `algotrading`, `cot-data`, `cftc`, `norgatedata`) to the repository's about section.
- [ ] **Create First Release**: Tag `v0.1.0` and use GitHub Releases to generate the release notes once ready to publish.
