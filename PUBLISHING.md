# Publishing to PyPI

This guide covers how to publish `python-jebao` to PyPI.

## Prerequisites

1. **PyPI Account**: Create an account at [pypi.org](https://pypi.org/account/register/)
2. **API Token**: Generate an API token at [pypi.org/manage/account/token/](https://pypi.org/manage/account/token/)
   - Scope: "Entire account" (or project-specific once published)
   - Save the token securely - you'll only see it once

## Setup

Install build tools:

```bash
pip install build twine
```

Or use the project's optional dependencies:

```bash
pip install -e ".[build]"
```

## Publishing Workflow

### 1. Update Version

Edit version in two places:
- `pyproject.toml` - line 7: `version = "x.y.z"`
- `jebao/__init__.py` - line 19: `__version__ = "x.y.z"`

Follow [Semantic Versioning](https://semver.org/):
- **MAJOR** (1.0.0): Incompatible API changes
- **MINOR** (0.1.0): New features, backwards compatible
- **PATCH** (0.0.1): Bug fixes, backwards compatible

### 2. Update Changelog

Document changes in README.md or create a CHANGELOG.md:

```markdown
## [0.1.1] - 2026-01-04
### Fixed
- Connection stability improvements
- Discovery timeout handling

### Added
- Support for Python 3.13
```

### 3. Clean Previous Builds

```bash
rm -rf dist/ build/ *.egg-info/
```

### 4. Build Distribution

```bash
python -m build
```

This creates:
- `dist/python_jebao-x.y.z-py3-none-any.whl` (wheel)
- `dist/python-jebao-x.y.z.tar.gz` (source distribution)

### 5. Test the Build (Optional)

Install locally to verify:

```bash
pip install dist/python_jebao-*.whl
```

### 6. Upload to PyPI

#### Using API Token (Recommended)

```bash
python -m twine upload dist/*
```

When prompted:
- **Username**: `__token__`
- **Password**: Your API token (starting with `pypi-`)

#### Using Username/Password

```bash
python -m twine upload dist/*
```

Enter your PyPI username and password when prompted.

### 7. Verify Upload

Visit: https://pypi.org/project/python-jebao/

Test installation:

```bash
pip install python-jebao
```

## Test PyPI (Recommended First Time)

Before publishing to production PyPI, test with TestPyPI:

### 1. Create TestPyPI Account

Register at [test.pypi.org](https://test.pypi.org/account/register/)

### 2. Upload to TestPyPI

```bash
python -m twine upload --repository testpypi dist/*
```

### 3. Test Installation

```bash
pip install --index-url https://test.pypi.org/simple/ python-jebao
```

### 4. If Successful, Upload to Production PyPI

```bash
python -m twine upload dist/*
```

## Configuration File (Optional)

Create `~/.pypirc` to avoid entering credentials each time:

```ini
[distutils]
index-servers =
    pypi
    testpypi

[pypi]
username = __token__
password = pypi-YourApiTokenHere

[testpypi]
repository = https://test.pypi.org/legacy/
username = __token__
password = pypi-YourTestApiTokenHere
```

**Security**: Ensure this file has restricted permissions:

```bash
chmod 600 ~/.pypirc
```

## Automated Publishing with GitHub Actions

Create `.github/workflows/publish.yml`:

```yaml
name: Publish to PyPI

on:
  release:
    types: [published]

jobs:
  publish:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install build tools
        run: pip install build twine

      - name: Build package
        run: python -m build

      - name: Publish to PyPI
        env:
          TWINE_USERNAME: __token__
          TWINE_PASSWORD: ${{ secrets.PYPI_API_TOKEN }}
        run: twine upload dist/*
```

Add your PyPI API token as a GitHub secret:
1. Go to repository Settings → Secrets → Actions
2. Add new secret: `PYPI_API_TOKEN`
3. Paste your PyPI API token

## Quick Reference Commands

```bash
# Clean build artifacts
rm -rf dist/ build/ *.egg-info/

# Build package
python -m build

# Upload to TestPyPI
python -m twine upload --repository testpypi dist/*

# Upload to PyPI
python -m twine upload dist/*

# Check package metadata
python -m twine check dist/*
```

## Troubleshooting

### "File already exists" Error

You cannot re-upload the same version. Either:
- Increment the version number
- Delete the version on PyPI (not recommended)

### Import Errors After Install

Ensure `pyproject.toml` has correct package configuration:

```toml
[tool.setuptools]
packages = ["jebao"]
```

### Missing Dependencies

Verify dependencies in `pyproject.toml`:

```toml
dependencies = [
    "netifaces>=0.11.0",
]
```

## Best Practices

1. **Always test locally** before publishing
2. **Use TestPyPI first** for new releases
3. **Version carefully** - you can't reuse version numbers
4. **Document changes** in changelog
5. **Tag releases** in git:
   ```bash
   git tag -a v0.1.0 -m "Release version 0.1.0"
   git push origin v0.1.0
   ```
6. **Create GitHub releases** to trigger automated publishing

## Resources

- [PyPI](https://pypi.org/)
- [TestPyPI](https://test.pypi.org/)
- [Python Packaging User Guide](https://packaging.python.org/)
- [Semantic Versioning](https://semver.org/)
