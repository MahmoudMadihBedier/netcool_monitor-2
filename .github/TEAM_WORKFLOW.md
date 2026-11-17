# Team Workflow and Governance

## Repository Structure

This repository follows a strict workflow to ensure code quality and maintain stability.

### Branches

- **main**: Production branch. Only the repository owner (@MahmoudMadihBedier) can merge to this branch.
- **feature/developer-1**: Development branch for Developer 1
- **feature/developer-2**: Development branch for Developer 2
- **feature/developer-3**: Development branch for Developer 3
- **feature/developer-4**: Development branch for Developer 4

## Roles and Permissions

### Repository Owner (@MahmoudMadihBedier)
- ✅ Can push to main branch
- ✅ Can merge pull requests to main
- ✅ Can modify branch protection rules
- ✅ Can manage team members and permissions
- ✅ Can approve and dismiss PR reviews
- ✅ Required reviewer for all code changes (see CODEOWNERS)

### Developers (Developer 1-4)
- ✅ Can pull from main branch
- ✅ Can push to their assigned feature branch (feature/developer-X)
- ✅ Can create pull requests from their feature branch to main
- ❌ Cannot push directly to main branch
- ❌ Cannot merge their own pull requests
- ❌ Cannot modify branch protection rules
- ❌ Cannot dismiss PR reviews

## Development Workflow

### 1. Getting Started

Each developer should:
1. Clone the repository
2. Switch to their assigned feature branch:
   ```bash
   git checkout feature/developer-X
   git pull origin feature/developer-X
   ```

### 2. Making Changes

1. Pull latest changes from main:
   ```bash
   git checkout main
   git pull origin main
   git checkout feature/developer-X
   git merge main
   ```

2. Create a feature branch from your assigned branch:
   ```bash
   git checkout -b feature/your-feature-name
   ```

3. Make your changes following the coding standards

4. Commit with conventional commit format:
   ```
   feat(scope): description
   fix(scope): description
   docs(scope): description
   test(scope): description
   refactor(scope): description
   chore(scope): description
   ```

5. Push to your feature branch:
   ```bash
   git push origin feature/your-feature-name
   ```

### 3. Creating Pull Requests

1. Go to GitHub and create a pull request from your feature branch to main
2. Fill in the PR template with:
   - Summary of changes
   - Type of change (feature, bugfix, etc.)
   - Test plan
   - Related issue number (if any)

3. Request review from @MahmoudMadihBedier

### 4. Review Process

1. Automated checks will run:
   - Code quality (Black, Ruff, MyPy, Pylint)
   - Security scan (Bandit, Safety)
   - Tests (pytest with coverage)

2. All checks must pass before review

3. @MahmoudMadihBedier will review the code:
   - Check code quality and style
   - Verify tests are adequate
   - Ensure documentation is updated
   - Validate the change aligns with project goals

4. Address review feedback:
   - Make requested changes
   - Push updates to your branch
   - Re-request review if needed

### 5. Merging

Only @MahmoudMadihBedier can merge PRs to main:
1. Ensure all checks pass
2. Ensure review is approved
3. Squash and merge (to maintain clean history)
4. Delete the feature branch after merge

## Quality Gates

### Before Submitting a PR

- [ ] Code follows project style guidelines
- [ ] All tests pass locally
- [ ] New features include tests
- [ ] Documentation is updated
- [ ] Commit messages follow conventional format
- [ ] Branch name follows convention (type/description)

### Automated Checks (Must Pass)

- **Code Quality**: Black formatting, Ruff linting, MyPy type checking
- **Security**: Bandit security scan, Safety dependency check
- **Tests**: All tests must pass with minimum coverage
- **PR Validation**: Description, branch naming, commit messages

### Manual Review Requirements

- Owner approval required (@MahmoudMadihBedier)
- Changes to core/, kernel/, main.py require additional scrutiny
- Changes to dependencies (requirements.txt) need justification
- Breaking changes require team discussion

## Code Review Guidelines

### For Reviewers (@MahmoudMadihBedier)

1. **Review the What**: Understand what the PR changes
2. **Review the Why**: Understand why the change is needed
3. **Review the How**: Check if the implementation is correct
4. **Check Tests**: Ensure adequate test coverage
5. **Check Documentation**: Verify docs are updated
6. **Check Style**: Ensure code follows project standards
7. **Be Constructive**: Provide clear, actionable feedback

### For Developers

1. **Keep PRs Small**: One PR should do one thing
2. **Add Context**: Explain why the change is needed
3. **Test Thoroughly**: Include tests for new functionality
4. **Respond Promptly**: Address review feedback quickly
5. **Ask Questions**: If unclear, ask for clarification

## Emergency Fixes

For critical production issues:
1. Create a hotfix branch from main
2. Make the minimal fix
3. Create PR with "hotfix" label
4. Expedited review process
5. Merge immediately after approval

## Communication

- Use GitHub issues for bugs and feature requests
- Use PR discussions for code-specific questions
- Important decisions should be documented in issues
- Team announcements in project discussions

## Branch Naming Convention

Follow this pattern: `type/description`

Types:
- `feature` - New functionality
- `bugfix` - Bug fixes
- `hotfix` - Critical production fixes
- `docs` - Documentation changes
- `refactor` - Code refactoring (no behavior change)
- `test` - Test additions or changes

Examples:
- `feature/add-user-authentication`
- `bugfix/fix-login-crash`
- `docs/update-readme`
- `refactor/simplify-config-loading`

## Commit Message Convention

Follow Conventional Commits:
```
type(scope): description

[optional body]

[optional footer]
```

Types:
- `feat` - New feature
- `fix` - Bug fix
- `docs` - Documentation
- `style` - Code style (formatting, etc.)
- `refactor` - Refactoring
- `test` - Tests
- `chore` - Maintenance tasks
- `perf` - Performance improvements

Examples:
- `feat(auth): add OAuth2 login support`
- `fix(ui): resolve crash on window resize`
- `docs(api): update authentication endpoint docs`

## Continuous Integration

All PRs trigger automated workflows:
1. **Quality Checks**: Linting, formatting, type checking
2. **Security Scan**: Vulnerability detection
3. **Tests**: Unit tests with coverage reporting
4. **PR Validation**: Description, naming, commit checks

These must all pass before a PR can be merged.

## Code of Conduct

1. Be respectful and professional
2. Provide constructive feedback
3. Focus on what is best for the project
4. Accept and give feedback gracefully
5. Help others learn and grow

## Questions?

Contact @MahmoudMadihBedier for:
- Permission issues
- Workflow clarifications
- Process improvements
- Urgent production issues
