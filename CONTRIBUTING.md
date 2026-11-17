# Contributing to Netcool Shift Monitor

Thank you for your interest in contributing! This document provides guidelines for contributing to this project.

## Getting Started

### Prerequisites

- Python 3.10 or higher
- Git
- GitHub account (if you don't have one, ask the repository owner for access)

### Setup

1. **Clone the repository**
   ```bash
   git clone https://github.com/MahmoudMadihBedier/netcool_monitor-2.git
   cd netcool_monitor-2
   ```

2. **Create a virtual environment**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   pip install pytest pytest-cov black ruff mypy pylint
   ```

4. **Switch to your assigned branch**
   ```bash
   git checkout feature/developer-X  # Replace X with your assigned number
   git pull origin feature/developer-X
   ```

## Development Workflow

### 1. Create a Feature Branch

From your assigned feature branch, create a new branch for your work:
```bash
git checkout feature/developer-X
git pull origin feature/developer-X
git checkout -b feature/your-feature-name
```

### 2. Make Your Changes

- Follow the existing code style and patterns
- Write tests for new functionality
- Update documentation as needed
- Keep changes focused and atomic

### 3. Code Quality Checks

Before committing, run the following checks locally:

```bash
# Format code with Black
black .

# Check linting with Ruff
ruff check .

# Type checking with MyPy
mypy --ignore-missing-imports .

# Run tests
pytest tests/ -v
```

### 4. Commit Your Changes

Use conventional commit format:
```bash
git add .
git commit -m "feat(scope): add new feature"
```

Commit types:
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation changes
- `style`: Code style changes
- `refactor`: Code refactoring
- `test`: Test changes
- `chore`: Maintenance tasks
- `perf`: Performance improvements

### 5. Push and Create Pull Request

```bash
git push origin feature/your-feature-name
```

Then go to GitHub and create a pull request to main.

## Pull Request Guidelines

### PR Description Template

```markdown
## Summary
Brief description of what this PR does and why.

## Type of Change
- [ ] Bug fix
- [ ] New feature
- [ ] Breaking change
- [ ] Documentation update

## Test Plan
- [ ] Tests pass locally
- [ ] New tests added for new functionality
- [ ] Manual testing completed

## Checklist
- [ ] Code follows project style guidelines
- [ ] Self-review completed
- [ ] Commented complex code
- [ ] Documentation updated
- [ ] No new warnings generated
- [ ] Added tests that prove fix is effective
- [ ] All tests passing
- [ ] Any dependent changes merged

## Related Issue
Closes #123
```

### PR Requirements

- [ ] Description includes Summary and Test Plan sections
- [ ] Branch name follows convention (type/description)
- [ ] Commit messages follow conventional format
- [ ] All automated checks pass
- [ ] Code is properly formatted (Black)
- [ ] No linting errors (Ruff)
- [ ] Tests pass with adequate coverage
- [ ] Security scan passes

## Code Style Guidelines

### Python Code

- Follow PEP 8 style guide
- Use type hints where appropriate
- Keep functions focused and small
- Add docstrings to public functions and classes
- Use meaningful variable and function names
- Avoid code duplication

### Documentation

- Update README.md for user-facing changes
- Add docstrings to new functions
- Comment complex logic
- Keep documentation in sync with code

## Testing Guidelines

### Writing Tests

- Write tests for all new functionality
- Test edge cases and error conditions
- Use descriptive test names
- Keep tests independent and fast
- Mock external dependencies

### Running Tests

```bash
# Run all tests
pytest tests/ -v

# Run specific test file
pytest tests/test_detector.py -v

# Run with coverage
pytest tests/ --cov=. --cov-report=html
```

## Branch Protection

The main branch is protected:
- Only @MahmoudMadihBedier can push to main
- All changes must go through pull requests
- PRs require approval from @MahmoudMadihBedier
- Automated checks must pass before merging

## Getting Help

If you need help:
1. Check the [TEAM_WORKFLOW.md](.github/TEAM_WORKFLOW.md) for detailed workflow information
2. Review existing code for patterns
3. Ask questions in PR discussions
4. Contact @MahmoudMadihBedier for permission or workflow issues

## Code Review Process

1. Submit your PR with complete description
2. Automated checks will run
3. Wait for review from @MahmoudMadihBedier
4. Address review feedback promptly
5. Update your branch with fixes
6. Re-request review when ready
7. PR will be merged after approval

## What Not to Do

- ❌ Push directly to main branch
- ❌ Merge your own PR
- ❌ Skip writing tests
- ❌ Ignore automated check failures
- ❌ Make large, monolithic PRs
- ❌ Commit sensitive data (API keys, passwords)
- ❌ Modify .github/ workflows without approval
- ❌ Change dependencies without justification

## Security Guidelines

- Never commit secrets or credentials
- Report security vulnerabilities privately to @MahmoudMadihBedier
- Follow security best practices
- Keep dependencies updated
- Review security scan results

## License

By contributing, you agree that your contributions will be licensed under the same license as the project.

## Questions?

If you have questions about contributing, please reach out to @MahmoudMadihBedier.
